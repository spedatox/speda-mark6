"""Forge peer launcher — SPEDA owns the Forge's lifecycle.

The Forge (Mark II, repo `forge-mk1`) is the standalone execution engine for
Optimus. When `forge_dir` is configured, the lifespan handler starts it as a
child process right after the backend's WebSocket routes are live:

    uv run --project <forge_dir> python -m forge connect --agent <forge_agent>

The Forge then connects back to this backend's agents WebSocket
(`WS /agents/ws/<forge_agent>`) like any external peer — no separate terminal,
no manual plumbing. The env the Forge needs (SPEDA key, WS URL, Cell backend,
workspace root, and a passed-through ANTHROPIC_API_KEY so the Forge makes its
OWN inference calls) is injected here from settings, so the SPEDA-side config is
the single place to wire the whole network.

Startup is best-effort by design: a missing directory, a broken interpreter, or
an instant crash logs a warning and SPEDA keeps running — the in-process Optimus
profile is the fallback, exactly as when the peer is offline. The launcher also
supervises: if the child exits while the app is up, it is relaunched with capped
exponential backoff so a transient Forge crash self-heals without a backend
restart.
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
    """Starts/stops/supervises the Forge peer child process.

    One instance lives on `app.state.forge_launcher`. `start()` spawns a
    supervisor task and returns immediately so it never blocks startup; `stop()`
    cancels the supervisor and terminates the child.
    """

    def __init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None
        self._supervisor: asyncio.Task | None = None
        self._stopping = False

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

    def _build_command(self, python: str) -> list[str]:
        # Precedence: explicit forge_python override → the repo's prepared venv →
        # `uv run` as a last resort. Each runs `python -m forge connect`.
        interp = python or self._venv_python()
        if interp:
            return [interp, "-m", "forge", "connect", "--agent", settings.forge_agent]
        return [
            "uv", "run", "--project", str(self._root),
            "python", "-m", "forge", "connect", "--agent", settings.forge_agent,
        ]

    def _build_env(self) -> dict[str, str]:
        return {
            **os.environ,
            "SPEDA_API_KEY": settings.speda_api_key,
            "SPEDA_WS_URL": settings.forge_ws_url,
            "FORGE_AGENT": settings.forge_agent,
            "FORGE_CELL_BACKEND": settings.forge_cell_backend,
            "FORGE_WORKSPACE_ROOT": str(self._root / ".forge" / "workspaces"),
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
        self._supervisor = asyncio.create_task(self._supervise())

    async def _supervise(self) -> None:
        """Keep the child alive while the app runs, with capped backoff."""
        python = (settings.forge_python or "").strip()
        cmd = self._build_command(python)
        env = self._build_env()
        backoff = _BACKOFF_START_S
        while not self._stopping:
            try:
                # stdout/stderr inherit the SPEDA console — the Forge's log lines
                # appear alongside the backend's, one terminal for the network.
                self._proc = await asyncio.create_subprocess_exec(
                    *cmd, cwd=str(self._root), env=env,
                )
            except (OSError, NotImplementedError) as e:
                logger.warning(
                    "forge_peer_start_failed",
                    extra={"dir": str(self._root), "cmd": cmd[0], "error": str(e)},
                )
                return  # unlaunchable (bad interpreter / no uv) — do not spin
            logger.info(
                "forge_peer_started",
                extra={"pid": self._proc.pid, "dir": str(self._root), "ws": settings.forge_ws_url},
            )
            rc = await self._proc.wait()
            self._proc = None
            if self._stopping:
                return
            logger.warning("forge_peer_exited", extra={"returncode": rc, "relaunch_in_s": backoff})
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                return
            backoff = min(backoff * 2, _BACKOFF_MAX_S)

    async def stop(self) -> None:
        self._stopping = True
        sup = self._supervisor
        self._supervisor = None
        if sup is not None:
            sup.cancel()
            try:
                await sup
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        proc = self._proc
        self._proc = None
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=_STOP_GRACE_S)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
            logger.info("forge_peer_stopped", extra={"returncode": proc.returncode})
        except ProcessLookupError:
            pass  # already gone
        except Exception as e:  # noqa: BLE001 — shutdown must complete regardless
            logger.warning("forge_peer_stop_failed", extra={"error": str(e)})
