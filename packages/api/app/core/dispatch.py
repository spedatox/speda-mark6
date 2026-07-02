"""
Inter-agent dispatch — the orchestrator-routed primitive (CLAUDE.md: agents
never talk to each other directly; everything goes through here).

An agent calls the dispatch_agent tool → AgentDispatcher.dispatch() runs the
TARGET agent's own orchestrator loop in-process (its profile, its prompt, its
model policy) and returns the final text to the CALLER. Multi-agent fan-out is
just the caller emitting several dispatch_agent tool calls in one turn — the
orchestrator already executes tool calls in parallel — or broadcast() under the
House Party Protocol, which rallies every in-process agent at once.

House Party Protocol (runtime flag, app/core/runtime_state.py): when engaged,
dispatched agents run at full interactive model grade instead of the background
tier, and broadcast dispatch ("all") becomes available. The flag is toggled by
the owner from the UI (POST /agents/house-party) or by an agent via the
house_party tool.

External peers (Optimus deployed standalone) are reached over the WebSocket
channel via WebSocketManager. A CONNECTED peer always outranks its in-process
profile — the profile remains as the identity layer and offline fallback. The
task is sent as a `task_dispatch` frame (optionally carrying a working
directory for coding work) and the result awaited on a correlated
`task_result` frame.

Every dispatch — and its result — is logged to the agent_messages table for the
comms tray (GET /agents/comms). Telemetry writes are best-effort: a logging
failure never fails the dispatch itself.

Construction: created BEFORE the CapabilityRegistry (the dispatch_agent skill
needs it at registration time), wired AFTER the orchestrator exists via wire().
All references live on app.state per Rule 6 — this module holds no globals.
"""

import asyncio
import logging
import time
import uuid
from datetime import datetime

from sqlalchemy import select

from app.core.context import AgentContext
from app.core.runtime_state import get_house_party
from app.database import AsyncSessionLocal
from app.models.agent_message import AgentMessage
from app.schemas.sse import SSEEventType

logger = logging.getLogger(__name__)

# An agent may dispatch, and the dispatched agent may dispatch once more —
# beyond that the chain is refused (runaway-fan-out guard, mirrors Rule 4a).
MAX_DISPATCH_DEPTH = 2
EXTERNAL_TIMEOUT_S = 180.0          # WebSocket peers must answer within this window
EXTERNAL_CODING_TIMEOUT_S = 600.0   # coding peers (Optimus) get room for real work
MAX_RESULT_CHARS = 12_000           # cap what flows back into the caller's context

# The group channel: how much recent network traffic a dispatched agent sees,
# and how hard each entry is truncated inside the transcript. Keeps the shared
# context useful without bloating every dispatch prompt.
CHANNEL_WINDOW = 20
CHANNEL_TASK_CHARS = 280
CHANNEL_RESULT_CHARS = 340


async def channel_transcript(
    limit: int = CHANNEL_WINDOW, agent: str | None = None, exclude_id: int | None = None,
) -> str:
    """
    The agent network's GROUP CHAT, rendered from agent_messages: every dispatch
    and its reply, oldest→newest, formatted as channel lines. Injected into each
    dispatched agent's context (so the roster shares one conversation) and
    served to the read_agent_channel skill. Returns "" when there is no traffic.
    """
    try:
        async with AsyncSessionLocal() as db:
            stmt = select(AgentMessage).order_by(AgentMessage.id.desc()).limit(max(1, min(limit, 60)))
            if exclude_id is not None:
                stmt = stmt.where(AgentMessage.id != exclude_id)
            if agent:
                from sqlalchemy import or_
                stmt = stmt.where(or_(AgentMessage.from_agent == agent, AgentMessage.to_agent == agent))
            rows = list((await db.execute(stmt)).scalars().all())
    except Exception as e:  # noqa: BLE001 — the channel is flavour, never load-bearing
        logger.warning("channel_transcript_failed", extra={"error": str(e)})
        return ""
    if not rows:
        return ""

    lines: list[str] = []
    for r in reversed(rows):  # oldest first, like a chat scrollback
        ts = r.created_at.strftime("%m-%d %H:%M")
        task = " ".join(r.task.split())[:CHANNEL_TASK_CHARS]
        lines.append(f"[{ts}] {r.from_agent.upper()} → {r.to_agent.upper()}: {task}")
        if r.status == "running":
            lines.append(f"         └ {r.to_agent.upper()}: … (still working)")
        elif r.result:
            result = " ".join(r.result.split())[:CHANNEL_RESULT_CHARS]
            tag = "" if r.status == "ok" else f" [{r.status.upper()}]"
            lines.append(f"         └ {r.to_agent.upper()}{tag}: {result}")
    return "\n".join(lines)


class AgentDispatcher:
    """Runs inter-agent tasks and logs the traffic. One instance on app.state."""

    def __init__(self) -> None:
        self._orchestrator = None
        self._profiles = None
        self._session_manager = None
        self._ws_manager = None
        # Correlation futures for external (WebSocket) dispatches, keyed by task_id.
        self._pending_external: dict[str, asyncio.Future] = {}

    def wire(self, *, orchestrator, profiles, session_manager, ws_manager) -> None:
        """Late-bind the engine refs (they are constructed after the registry)."""
        self._orchestrator = orchestrator
        self._profiles = profiles
        self._session_manager = session_manager
        self._ws_manager = ws_manager

    # ── Public API ───────────────────────────────────────────────────────────

    def known_agents(self) -> list[str]:
        """agent_ids of every dispatchable in-process profile (for tool schemas /
        validation). Session-scope aliases (dispatch_target=False) are excluded —
        they take /chat requests but are never dispatch targets."""
        if self._profiles is None:
            return []
        return [p.agent_id for p in self._profiles.roster() if p.dispatch_target]

    async def dispatch(
        self,
        *,
        from_agent: str,
        to_agent: str,
        task: str,
        user_id: int,
        request_id: str,
        depth: int = 0,
        cwd: str | None = None,
    ) -> str:
        """
        Run `task` on `to_agent` and return its final text to the caller.
        Never raises — every failure path returns an explanatory string the
        calling model can reason over (same tolerance pattern as skills).
        """
        if self._orchestrator is None:
            return "Dispatch engine is not wired yet — backend still starting up."
        if to_agent == from_agent:
            return "Refused: you cannot dispatch a task to yourself. Do it directly."
        if depth >= MAX_DISPATCH_DEPTH:
            return (
                f"Refused: dispatch depth limit ({MAX_DISPATCH_DEPTH}) reached. "
                "Complete the task yourself instead of delegating further."
            )

        protocol = "house_party" if get_house_party() else "direct"
        msg_id = await self._log_start(
            request_id=request_id, from_agent=from_agent, to_agent=to_agent,
            kind="dispatch", protocol=protocol, task=task,
        )
        started = time.monotonic()

        # External-first: a connected standalone peer (Optimus) always outranks
        # its in-process profile — the profile stays registered as the identity
        # layer and the offline fallback, never as a competing engine.
        profile = self._profiles.get(to_agent) if self._profiles else None
        external = self._ws_manager is not None and self._ws_manager.is_connected(to_agent)
        session_id = None
        if external:
            result, status = await self._run_external(
                to_agent=to_agent, from_agent=from_agent, task=task, cwd=cwd,
            )
            if status in ("offline", "error") and profile is not None:
                # Peer vanished between the presence check and the send —
                # degrade to the in-process profile rather than failing.
                result, status, session_id = await self._run_in_process(
                    profile=profile, from_agent=from_agent, task=task,
                    user_id=user_id, request_id=request_id, depth=depth,
                    house_party=(protocol == "house_party"), own_msg_id=msg_id,
                )
        elif profile is not None:
            result, status, session_id = await self._run_in_process(
                profile=profile, from_agent=from_agent, task=task,
                user_id=user_id, request_id=request_id, depth=depth,
                house_party=(protocol == "house_party"), own_msg_id=msg_id,
            )
        else:
            result, status = await self._run_external(
                to_agent=to_agent, from_agent=from_agent, task=task, cwd=cwd,
            )

        await self._log_finish(
            msg_id, status=status, result=result, session_id=session_id,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        logger.info(
            "agent_dispatch",
            extra={
                "request_id": request_id, "from": from_agent, "to": to_agent,
                "status": status, "protocol": protocol, "depth": depth,
            },
        )
        return result

    async def broadcast(
        self,
        *,
        from_agent: str,
        task: str,
        user_id: int,
        request_id: str,
        depth: int = 0,
    ) -> str:
        """
        House Party fan-out: run `task` on every in-process agent except the
        caller, in parallel, and return the combined transcripts. Only available
        while the House Party Protocol is engaged — direct one-to-one dispatch
        needs no protocol.
        """
        if not get_house_party():
            return (
                "Refused: broadcast dispatch requires the House Party Protocol. "
                "Ask the owner to engage it (comms tray or /agents/house-party), "
                "or engage it with the house_party tool if the owner asked for it."
            )
        targets = [a for a in self.known_agents() if a != from_agent]
        if not targets:
            return "No other agents are registered."

        await self._log_start(
            request_id=request_id, from_agent=from_agent, to_agent="all",
            kind="broadcast", protocol="house_party", task=task, status="ok",
        )
        results = await asyncio.gather(*[
            self.dispatch(
                from_agent=from_agent, to_agent=t, task=task,
                user_id=user_id, request_id=request_id, depth=depth,
            )
            for t in targets
        ])
        parts = [f"### {t.upper()}\n{r}" for t, r in zip(targets, results)]
        return "House Party broadcast complete. Responses:\n\n" + "\n\n".join(parts)

    def resolve_external_result(self, task_id: str, result: str, status: str = "ok") -> bool:
        """Called by the agents WebSocket router when a `task_result` frame
        arrives from an external peer. Returns True if a dispatch was waiting."""
        fut = self._pending_external.get(task_id)
        if fut is None or fut.done():
            return False
        fut.set_result((result, status if status in ("ok", "error") else "ok"))
        return True

    # ── In-process path ──────────────────────────────────────────────────────

    async def _run_in_process(
        self, *, profile, from_agent: str, task: str,
        user_id: int, request_id: str, depth: int, house_party: bool,
        own_msg_id: int | None = None,
    ) -> tuple[str, str, int | None]:
        """Run the target agent's own orchestrator loop; returns (text, status, session_id)."""
        # House Party = full interactive grade across all agents (D-C4); normal
        # agent-to-agent traffic runs on the background tier. The profile owns
        # the actual model IDs either way (Rule 10).
        model = profile.allocate_model("user" if house_party else "agent")

        # The GROUP CHANNEL: recent network traffic, shared by the whole roster —
        # the dispatched agent joins the conversation instead of waking up with
        # amnesia. Its own incoming task is excluded (it follows as TASK below).
        channel = await channel_transcript(exclude_id=own_msg_id)
        channel_block = (
            "\n\nAGENT NETWORK CHANNEL — recent traffic between all agents "
            "(oldest first). Use it for context and continuity; do not repeat "
            f"work already answered here:\n{channel}"
        ) if channel else ""
        try:
            async with AsyncSessionLocal() as db:
                session = await self._session_manager.get_or_create(
                    db=db, user_id=user_id, triggered_by="agent",
                    model_used=model, agent_id=profile.agent_id,
                )
                context = AgentContext(
                    agent_id=profile.agent_id,
                    user_id=user_id,
                    session_id=session.id,
                    request_id=request_id,
                    triggered_by="agent",
                    trigger_payload={"from_agent": from_agent, "task": task},
                    output_mode="silent",
                    model=model,
                    system_prompt="",
                    conversation_history=[{
                        "role": "user",
                        "content": (
                            f"Inter-agent dispatch from {from_agent.upper()}. Complete "
                            "the task below and reply with the result — your answer "
                            f"goes back to {from_agent.upper()}, not to the owner, so "
                            "skip greetings and pleasantries and lead with the "
                            "substance. Be complete but compact."
                            f"{channel_block}\n\n"
                            f"TASK: {task}"
                        ),
                    }],
                    db=db,
                    timezone="UTC",
                    extra={"dispatch_depth": depth + 1},
                )

                chunks: list[str] = []
                errors: list[str] = []
                async for event in self._orchestrator.run(context):
                    if event.type == SSEEventType.CHUNK and isinstance(event.data, str):
                        chunks.append(event.data)
                    elif event.type == SSEEventType.ERROR:
                        errors.append(str(event.data))

                text = "".join(chunks).strip()[:MAX_RESULT_CHARS]
                if text:
                    return text, "ok", session.id
                if errors:
                    return f"{profile.agent_id} failed: {'; '.join(errors)}", "error", session.id
                return f"{profile.agent_id} returned an empty response.", "error", session.id
        except Exception as e:  # noqa: BLE001 — dispatch must never raise into the caller's loop
            logger.error(
                "agent_dispatch_error",
                extra={"request_id": request_id, "to": profile.agent_id, "error": str(e)},
            )
            return f"Dispatch to {profile.agent_id} failed: {e}", "error", None

    # ── External (WebSocket peer) path ───────────────────────────────────────

    async def _run_external(
        self, *, to_agent: str, from_agent: str, task: str, cwd: str | None = None,
    ) -> tuple[str, str]:
        """Dispatch to a standalone peer (Optimus) over its WebSocket connection."""
        if self._ws_manager is None or not self._ws_manager.is_connected(to_agent):
            return (
                f"'{to_agent}' is not a registered agent and no external peer by "
                "that name is connected. Do not retry until it comes online.",
                "offline",
            )
        # Coding peers need room for multi-step tool loops; everything else
        # keeps the tight default so a dead peer can't stall its caller long.
        timeout = EXTERNAL_CODING_TIMEOUT_S if to_agent == "optimus" else EXTERNAL_TIMEOUT_S
        task_id = str(uuid.uuid4())
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending_external[task_id] = fut
        try:
            await self._ws_manager.send(to_agent, {
                "type": "task_dispatch",
                "task_id": task_id,
                "from": from_agent,
                "task": task,
                "cwd": cwd,
                "permission_mode": None,
            })
            result, status = await asyncio.wait_for(fut, timeout)
            return str(result)[:MAX_RESULT_CHARS], status
        except asyncio.TimeoutError:
            return (
                f"{to_agent} did not answer within {int(timeout)}s. "
                "The task may still be running on the peer; do not blindly retry.",
                "timeout",
            )
        except Exception as e:  # noqa: BLE001
            return f"Dispatch to external peer {to_agent} failed: {e}", "error"
        finally:
            self._pending_external.pop(task_id, None)

    # ── Telemetry (best-effort — never fails a dispatch) ─────────────────────

    async def _log_start(
        self, *, request_id: str, from_agent: str, to_agent: str,
        kind: str, protocol: str, task: str, status: str = "running",
    ) -> int | None:
        try:
            async with AsyncSessionLocal() as db:
                row = AgentMessage(
                    request_id=request_id, from_agent=from_agent, to_agent=to_agent,
                    kind=kind, protocol=protocol, task=task[:4000], status=status,
                    created_at=datetime.utcnow(),
                )
                db.add(row)
                await db.commit()
                return row.id
        except Exception as e:  # noqa: BLE001
            logger.warning("agent_message_log_failed", extra={"error": str(e)})
            return None

    async def _log_finish(
        self, msg_id: int | None, *, status: str, result: str,
        session_id: int | None, duration_ms: int,
    ) -> None:
        if msg_id is None:
            return
        try:
            async with AsyncSessionLocal() as db:
                row = await db.get(AgentMessage, msg_id)
                if row is None:
                    return
                row.status = status
                row.result = result[:8000]
                row.session_id = session_id
                row.duration_ms = duration_ms
                await db.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("agent_message_log_failed", extra={"error": str(e)})
