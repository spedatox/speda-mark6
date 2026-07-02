"""
ExternalAgentProxy — streams an interactive chat turn through an external
WebSocket peer (Optimus standalone) and re-emits its events as SSEEvents.

This is the chat-side counterpart of AgentDispatcher._run_external: where the
dispatcher fires one task and awaits one result, the proxy runs a full
user-facing turn — the peer streams `chat_event` frames (chunk/tool/
tool_result/done/error, correlated by chat_id) and run() yields them with the
exact same SSEEvent contract as AgentOrchestrator.run(). The chat router picks
which engine feeds the response stream; everything downstream (persistence,
background tasks, the UI) is unchanged.

The peer is stateless per turn: the full Anthropic-format history goes over
the socket every time (the backend DB is the source of truth — truncate/
regenerate/edit all mutate it server-side, so a stateful peer session would
drift). Frames are routed here by the agents WebSocket route via deliver();
a peer disconnect mid-stream fails all of its in-flight chats via fail_agent()
so the UI gets a terminal ERROR instead of a hang.

One instance lives on app.state.agent_proxy (Rule 6).
"""

import asyncio
import logging
import uuid
from typing import AsyncGenerator

from app.core.context import AgentContext
from app.schemas.sse import SSEEvent, SSEEventType
from app.websocket.manager import WebSocketManager

logger = logging.getLogger(__name__)

# Max silence BETWEEN events, not total runtime — a coding run may legitimately
# take many minutes, but the peer streams tool/chunk events throughout; a long
# gap means it died without a terminal frame.
IDLE_TIMEOUT_S = 300.0

# chat_event "type" → SSEEventType. Deliberately 1:1 — the peer speaks the SSE
# vocabulary so this proxy stays a dumb re-wrapper.
_EVENT_MAP = {
    "chunk": SSEEventType.CHUNK,
    "tool": SSEEventType.TOOL,
    "tool_result": SSEEventType.TOOL_RESULT,
    "done": SSEEventType.DONE,
    "error": SSEEventType.ERROR,
}

_TERMINAL = frozenset({"done", "error"})


class ExternalAgentProxy:
    """Correlates proxied chat streams over the shared agent WebSocket."""

    def __init__(self, ws_manager: WebSocketManager) -> None:
        self._ws = ws_manager
        self._pending: dict[str, asyncio.Queue] = {}   # chat_id → event queue
        self._owner: dict[str, str] = {}               # chat_id → agent_id

    # ── Frame ingress (called from the agents WebSocket route) ──────────────

    def deliver(self, chat_id: str, event: dict) -> bool:
        """Route one incoming `chat_event` to its waiting stream. Returns False
        when nothing is waiting (late frame after cancel/timeout — dropped)."""
        queue = self._pending.get(chat_id)
        if queue is None:
            return False
        queue.put_nowait(event)
        return True

    def fail_agent(self, agent_id: str) -> None:
        """Peer disconnected: terminate every chat it owned with an ERROR so no
        stream hangs until the idle timeout."""
        for chat_id, owner in list(self._owner.items()):
            if owner != agent_id:
                continue
            queue = self._pending.get(chat_id)
            if queue is not None:
                queue.put_nowait({
                    "type": "error",
                    "data": f"{agent_id.title()} disconnected mid-stream. "
                            "The task may be incomplete.",
                })

    # ── Engine (same contract as AgentOrchestrator.run) ─────────────────────

    async def run(self, context: AgentContext) -> AsyncGenerator[SSEEvent, None]:
        """Proxy one chat turn to the external peer, yielding SSEEvents."""
        agent_id = context.agent_id
        chat_id = str(uuid.uuid4())
        queue: asyncio.Queue = asyncio.Queue()
        self._pending[chat_id] = queue
        self._owner[chat_id] = agent_id
        terminal_seen = False

        try:
            await self._ws.send(agent_id, {
                "type": "chat_request",
                "chat_id": chat_id,
                "session_id": context.session_id,
                "request_id": context.request_id,
                "history": context.conversation_history,
                # The user's explicit UI model pick, if any — None lets the
                # peer apply its own model policy (Rule 10: no model IDs here).
                "model": context.extra.get("user_model"),
                "cwd": context.extra.get("cwd"),
                "system_prompt": None,
            })
            yield SSEEvent(
                type=SSEEventType.START, data="",
                session_id=context.session_id, request_id=context.request_id,
            )

            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), IDLE_TIMEOUT_S)
                except asyncio.TimeoutError:
                    logger.error(
                        "external_chat_idle_timeout",
                        extra={"request_id": context.request_id,
                               "agent_id": agent_id, "chat_id": chat_id},
                    )
                    yield SSEEvent(
                        type=SSEEventType.ERROR,
                        data=f"{agent_id.title()} stopped responding mid-task "
                             f"(no events for {int(IDLE_TIMEOUT_S)}s).",
                        session_id=context.session_id,
                        request_id=context.request_id,
                    )
                    return

                etype = str(event.get("type", ""))
                sse_type = _EVENT_MAP.get(etype)
                if sse_type is None:
                    logger.warning(
                        "external_chat_unknown_event",
                        extra={"request_id": context.request_id, "type": etype},
                    )
                    continue

                yield SSEEvent(
                    type=sse_type,
                    data=event.get("data", "" if etype in ("chunk", "error") else {}),
                    session_id=context.session_id,
                    request_id=context.request_id,
                )
                if etype in _TERMINAL:
                    terminal_seen = True
                    return
        finally:
            self._pending.pop(chat_id, None)
            self._owner.pop(chat_id, None)
            if not terminal_seen:
                # Abnormal end (client disconnect / idle timeout / error in the
                # SSE pipeline): tell the peer to stop burning tokens.
                try:
                    await self._ws.send(agent_id, {
                        "type": "chat_cancel", "chat_id": chat_id,
                    })
                except Exception:  # noqa: BLE001 — best-effort by design
                    pass
