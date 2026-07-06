"""
External chat proxy — interactive turns for standalone WebSocket peers (Optimus).

The in-process agents run through AgentOrchestrator; Optimus runs on its own
framework and connects via WebSocketManager. This proxy makes /chat/optimus
behave natively anyway: the router hands it the SAME session-backed history it
would give the orchestrator, the proxy sends one `chat_request` frame over the
peer socket, and the peer streams `chat_event` frames back (chunk / tool /
tool_result / done / error) which are re-emitted as SSEEvents 1:1. Sessions,
persistence, title generation, and memory extraction all stay on this side —
which is exactly what makes Optimus share the Mark VI memory like any other
agent.

Owner memory and the agent network channel are injected via `extra_context`
(the peer appends it to its own identity prompt — it never replaces it; the
peer owns its identity per Rule 10's spirit).

One instance on app.state (Rule 6). The agents WebSocket router delivers
incoming chat_event frames via deliver().
"""

import asyncio
import logging
from typing import AsyncGenerator

from app.core.dispatch import channel_transcript
from app.schemas.sse import SSEEvent, SSEEventType
from app.skills.memory import recall_for_context

logger = logging.getLogger(__name__)

# The peer streams deltas continuously while working; a long gap means the
# turn died on the other side (crash, hung tool). Generous because a single
# local build/test tool call can legitimately run for minutes.
IDLE_TIMEOUT_S = 300.0


class ExternalChatProxy:
    """Correlates one interactive turn per chat_id across the peer socket."""

    def __init__(self, ws_manager) -> None:
        self._ws = ws_manager
        self._pending: dict[str, asyncio.Queue] = {}

    # ── Called by the agents WebSocket router ───────────────────────────────

    def deliver(self, chat_id: str, event: dict) -> bool:
        """Route one incoming chat_event to the waiting turn. Returns False if
        no turn is waiting (stale frame after a timeout/disconnect)."""
        queue = self._pending.get(chat_id)
        if queue is None:
            return False
        queue.put_nowait(event)
        return True

    # ── Called by the chat router ────────────────────────────────────────────

    @staticmethod
    async def build_peer_context(user_id: int, db) -> str:
        """Owner memory + agent network channel — the Mark VI context every
        in-process agent gets, packaged for the peer's system prompt."""
        parts: list[str] = []
        try:
            memory = await recall_for_context(user_id, db)
            if memory:
                parts.append(memory)
        except Exception as e:  # noqa: BLE001 — memory must never break a chat
            logger.warning("peer_memory_recall_failed", extra={"error": str(e)})
        try:
            channel = await channel_transcript()
            if channel:
                parts.append(
                    "AGENT NETWORK CHANNEL — recent traffic between all agents "
                    "(oldest first). Use it for context and continuity:\n" + channel
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("peer_channel_failed", extra={"error": str(e)})
        return "\n\n".join(parts)

    async def respond(self, agent_id: str, interaction_id: str, response: dict) -> None:
        """Forward the owner's answer to a question/permission prompt back to
        the peer as a chat_interaction frame."""
        await self._ws.send(agent_id, {
            "type": "chat_interaction",
            "interaction_id": interaction_id,
            "response": response,
        })

    async def run_turn(
        self,
        *,
        agent_id: str,
        history: list,
        model: str | None,
        extra_context: str,
        session_id: int,
        request_id: str,
        cwd: str | None = None,
        permission_mode: str | None = None,
    ) -> AsyncGenerator[SSEEvent, None]:
        """Run one interactive turn on the peer; yield SSEEvents until the
        terminal done/error. request_id doubles as the chat correlation id."""
        chat_id = request_id
        queue: asyncio.Queue = asyncio.Queue()
        self._pending[chat_id] = queue

        def _ev(type_: SSEEventType, data) -> SSEEvent:
            return SSEEvent(type=type_, data=data, session_id=session_id, request_id=request_id)

        finished = False
        try:
            await self._ws.send(agent_id, {
                "type": "chat_request",
                "chat_id": chat_id,
                "history": history,
                # Optimus's LLM layer is a port of ours — same "provider:model"
                # ref format — so the resolved pick (user's selection or the
                # owner's per-agent pin) travels as-is.
                "model": model,
                "extra_context": extra_context,
                # Owner-chosen workspace + permission mode (default / accept-
                # edits / bypass / plan). The peer treats the mode as a floor:
                # it can only tighten its configured ceiling.
                "cwd": cwd,
                "permission_mode": permission_mode,
            })
            yield _ev(SSEEventType.START, {})

            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=IDLE_TIMEOUT_S)
                except asyncio.TimeoutError:
                    finished = True
                    yield _ev(
                        SSEEventType.ERROR,
                        f"{agent_id} stopped responding (no activity for "
                        f"{int(IDLE_TIMEOUT_S)}s). The peer link may have dropped.",
                    )
                    return

                kind = event.get("type")
                data = event.get("data")
                if kind == "done":
                    finished = True
                    yield _ev(SSEEventType.DONE, {})
                    return
                if kind == "error":
                    finished = True
                    yield _ev(SSEEventType.ERROR, str(data))
                    return
                if kind == "chunk":
                    yield _ev(SSEEventType.CHUNK, str(data))
                elif kind == "tool":
                    yield _ev(SSEEventType.TOOL, data if isinstance(data, dict) else {})
                elif kind == "tool_result":
                    yield _ev(SSEEventType.TOOL_RESULT, data if isinstance(data, dict) else {})
                elif kind == "question":
                    yield _ev(SSEEventType.QUESTION, data if isinstance(data, dict) else {})
                elif kind == "permission":
                    yield _ev(SSEEventType.PERMISSION, data if isinstance(data, dict) else {})
                elif kind == "ping":
                    # Keepalive while the owner answers a prompt — forwarded so
                    # the UI's stream watchdog stays calm too.
                    yield _ev(SSEEventType.PING, {})
                # unknown kinds are dropped silently — forward compatibility

        finally:
            self._pending.pop(chat_id, None)
            if not finished:
                # Client disconnected mid-turn — tell the peer to stop working.
                try:
                    await self._ws.send(agent_id, {"type": "chat_cancel", "chat_id": chat_id})
                except Exception:  # noqa: BLE001 — peer may be gone too
                    pass
