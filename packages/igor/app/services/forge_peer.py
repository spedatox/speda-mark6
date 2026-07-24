"""Forge peer launcher — SPEDA owns the Forge's lifecycle.

The Forge (Mark II, repo `forge-mk1`) is the standalone execution engine behind
the external-backend agents (Optimus, Centurion). When `forge_dir` is configured,
the lifespan handler starts ONE child process per agent in `forge_agents`, right
after the backend's WebSocket routes are live:

    uv run --project <forge_dir> python -m forge connect --agent <agent_id>

Each child then connects back to this backend's agents WebSocket
(`WS /agents/ws/<agent_id>`) like any external peer — no separate terminal, no
manual plumbing. The env each child needs (SPEDA key, its own WS URL, Cell
backend, an isolated workspace root, and a passed-through ANTHROPIC_API_KEY so
the Forge makes its OWN inference calls) is injected here from settings, so the
SPEDA-side config is the single place to wire the whole network.

Peers register under their `agent_id`, and Mark VI keys connections by that id
(`WebSocketManager`), so each peer MUST connect on the WS path matching its own
id — otherwise disconnect teardown (which uses the path id) and chat routing
(which checks the registration id) disagree. The launcher builds every peer's
URL from `forge_ws_url` as a base plus `/<agent_id>` to keep the two aligned.

Startup is best-effort by design: a missing directory, a broken interpreter, or
an instant crash logs a warning and SPEDA keeps running — each agent's in-process
profile is the fallback, exactly as when its peer is offline. The launcher also
supervises: if a child exits while the app is up, it is relaunched with capped
exponential backoff so a transient Forge crash self-heals without a backend
restart. Each agent is supervised independently — one peer crashing never takes
another down.
"""

import asyncio
import logging
import os
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

_STOP_GRACE_S = 5.0
_BACKOFF_START_S = 2.0
_BACKOFF_MAX_S = 60.0


class ForgePeerLauncher:
    """Starts/stops/supervises one Forge peer child process per agent.

    One instance lives on `app.state.forge_launcher`. `start()` spawns a
    supervisor task per configured agent and returns immediately so it never
    blocks startup; `stop()` cancels every supervisor and terminates each child.
    """

    def __init__(self) -> None:
        # agent_id → running child process (present only while alive).
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        # agent_id → its supervisor task.
        self._supervisors: dict[str, asyncio.Task] = {}
        self._stopping = False
        self._root: Path | None = None

    # ── Configuration ─────────────────────────────────────────────────────────
    def _agents(self) -> list[str]:
        """The agents to back with a peer, de-duped and order-preserving.

        `forge_agents` (plural) is the source of truth; `forge_agent` (singular)
        is the legacy fallback used only when the plural setting is left blank."""
        raw = (settings.forge_agents or "").strip() or (settings.forge_agent or "").strip()
        seen: set[str] = set()
        out: list[str] = []
        for part in raw.split(","):
            agent = part.strip()
            if agent and agent not in seen:
                seen.add(agent)
                out.append(agent)
        return out

    def _ws_base(self) -> str:
        """`forge_ws_url` as a base, tolerant of a legacy trailing agent segment.

        Old configs set the full per-agent URL (…/agents/ws/optimus). New configs
        set the base (…/agents/ws). Either works: if the last path segment is one
        of the agents we are launching (or the legacy singular `forge_agent`), it
        is stripped so we can append the right id per peer."""
        base = (settings.forge_ws_url or "").rstrip("/")
        head, _, tail = base.rpartition("/")
        if head and tail in set(self._agents()) | {(settings.forge_agent or "").strip()}:
            return head
        return base

    def _ws_url_for(self, agent_id: str) -> str:
        return f"{self._ws_base()}/{agent_id}"

    def _resolve_dir(self) -> Path | None:
        peer_dir = (settings.forge_dir or "").strip()
        if not settings.forge_autostart or not peer_dir:
            logger.info("forge_peer_autostart_disabled")
            return None
        root = Path(peer_dir)
        if not (root / "forge" / "__main__.py").exists():
            logger.warning(
                "forge_dir_invalid",
                extra={"dir": peer_dir, "hint": "expected <dir>/forge/__main__.py"},
            )
            return None
        return root

    def _venv_python(self) -> str | None:
        """The Forge repo's own venv interpreter, if one has been prepared.

        Preferred over `uv run` because the Forge's optional `graph` extra pins a
        graphify version that isn't published yet, which makes uv's universal-lock
        resolution fail for the whole project. A venv built with `uv pip install`
        (base deps only) sidesteps that entirely.
        """
        for rel in ("Scripts/python.exe", "bin/python"):
            cand = self._root / ".venv" / rel
            if cand.exists():
                return str(cand)
        return None

    def _build_command(self, python: str, agent_id: str) -> list[str]:
        # Precedence: explicit forge_python override → the repo's prepared venv →
        # `uv run` as a last resort. Each runs `python -m forge connect`.
        interp = python or self._venv_python()
        if interp:
            return [interp, "-m", "forge", "connect", "--agent", agent_id]
        return [
            "uv", "run", "--project", str(self._root),
            "python", "-m", "forge", "connect", "--agent", agent_id,
        ]

    def _build_env(self, agent_id: str) -> dict[str, str]:
        return {
            **os.environ,
            "SPEDA_API_KEY": settings.speda_api_key,
            "SPEDA_WS_URL": self._ws_url_for(agent_id),
            "FORGE_AGENT": agent_id,
            "FORGE_CELL_BACKEND": settings.forge_cell_backend,
            # One workspace root per agent — peers share the repo but never each
            # other's working tree, so concurrent jobs can't clobber each other.
            "FORGE_WORKSPACE_ROOT": str(self._root / ".forge" / "workspaces" / agent_id),
            # The Forge holds its own credentials and makes its own inference
            # calls — Mark VI is a job source, never an inference proxy.
            "ANTHROPIC_API_KEY": settings.anthropic_api_key,
        }

    async def start(self) -> None:
        root = self._resolve_dir()
        if root is None:
            return
        self._root = root
        self._stopping = False
        agents = self._agents()
        if not agents:
            logger.warning("forge_peer_no_agents")
            return
        for agent_id in agents:
            self._supervisors[agent_id] = asyncio.create_task(self._supervise(agent_id))
        logger.info("forge_peers_launching", extra={"agents": agents, "dir": str(root)})

    async def _supervise(self, agent_id: str) -> None:
        """Keep one agent's child alive while the app runs, with capped backoff."""
        python = (settings.forge_python or "").strip()
        cmd = self._build_command(python, agent_id)
        env = self._build_env(agent_id)
        ws_url = self._ws_url_for(agent_id)
        backoff = _BACKOFF_START_S
        while not self._stopping:
            try:
                # stdout/stderr inherit the SPEDA console — the Forge's log lines
                # appear alongside the backend's, one terminal for the network.
                proc = await asyncio.create_subprocess_exec(
                    *cmd, cwd=str(self._root), env=env,
                )
            except (OSError, NotImplementedError) as e:
                logger.warning(
                    "forge_peer_start_failed",
                    extra={"agent_id": agent_id, "dir": str(self._root),
                           "cmd": cmd[0], "error": str(e)},
                )
                return  # unlaunchable (bad interpreter / no uv) — do not spin
            self._procs[agent_id] = proc
            logger.info(
                "forge_peer_started",
                extra={"agent_id": agent_id, "pid": proc.pid,
                       "dir": str(self._root), "ws": ws_url},
            )
            rc = await proc.wait()
            self._procs.pop(agent_id, None)
            if self._stopping:
                return
            logger.warning(
                "forge_peer_exited",
                extra={"agent_id": agent_id, "returncode": rc, "relaunch_in_s": backoff},
            )
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                return
            backoff = min(backoff * 2, _BACKOFF_MAX_S)

    async def stop(self) -> None:
        self._stopping = True
        supervisors = list(self._supervisors.values())
        self._supervisors.clear()
        for sup in supervisors:
            sup.cancel()
        if supervisors:
            await asyncio.gather(*supervisors, return_exceptions=True)
        procs = list(self._procs.items())
        self._procs.clear()
        for agent_id, proc in procs:
            if proc.returncode is not None:
                continue
            try:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=_STOP_GRACE_S)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
                logger.info(
                    "forge_peer_stopped",
                    extra={"agent_id": agent_id, "returncode": proc.returncode},
                )
            except ProcessLookupError:
                pass  # already gone
            except Exception as e:  # noqa: BLE001 — shutdown must complete regardless
                logger.warning(
                    "forge_peer_stop_failed",
                    extra={"agent_id": agent_id, "error": str(e)},
                )
