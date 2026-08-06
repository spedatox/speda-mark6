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
from app.core.peer_routing import resolve
from app.schemas.sse import SSEEvent, SSEEventType
from app.skills.memory import recall_for_context
from app.websocket.manager import WebSocketManager

logger = logging.getLogger(__name__)

# Max silence BETWEEN events, not total runtime — a coding run may legitimately
# take many minutes, but the peer streams tool/chunk events throughout; a long
# gap means it died without a terminal frame.
IDLE_TIMEOUT_S = 300.0

# chat_event "type" → SSEEventType. The type map is 1:1 — the peer speaks the
# SSE vocabulary. Payloads are 1:1 too EXCEPT tool_result: the peer emits the
# Anthropic-native shape ({tool_use_id, is_error, content}) while every consumer
# — both clients' live renderers and the turn runner that persists the turn for
# history — reads the orchestrator's shape ({id, result}). _normalize_tool_result
# below bridges that so a proxied result renders and saves like an in-process one.
_EVENT_MAP = {
    "chunk": SSEEventType.CHUNK,
    "tool": SSEEventType.TOOL,
    "tool_result": SSEEventType.TOOL_RESULT,
    "permission_request": SSEEventType.PERMISSION_REQUEST,
    "done": SSEEventType.DONE,
    "error": SSEEventType.ERROR,
}

_TERMINAL = frozenset({"done", "error"})

# Match AgentOrchestrator's tool_result preview cap so the disclosure panel holds
# the same amount of output whichever engine ran the turn.
_RESULT_PREVIEW_CHARS = 1500


def _stringify_content(content: object) -> str:
    """Flatten an Anthropic tool_result `content` into display text. The peer may
    send a plain string or a list of content blocks
    ([{"type": "text", "text": ...}, ...]); every consumer wants one string."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            str(b.get("text", b.get("content", ""))) if isinstance(b, dict) else str(b)
            for b in content
        ]
        return "\n".join(p for p in parts if p)
    return str(content)


def _normalize_tool_result(data: object) -> dict:
    """Peer tool_result payload → the canonical {id, result} the clients and the
    turn runner read. Tolerant of either shape so it is a no-op if the peer is
    ever updated to emit {id, result} directly: it reads `id`/`result` first and
    falls back to the peer's `tool_use_id`/`content`."""
    if not isinstance(data, dict):
        return {"id": None, "result": _stringify_content(data)[:_RESULT_PREVIEW_CHARS]}
    tool_id = data.get("id", data.get("tool_use_id"))
    raw = data.get("result", data.get("content"))
    return {"id": tool_id, "result": _stringify_content(raw)[:_RESULT_PREVIEW_CHARS]}


class ExternalAgentProxy:
    """Correlates proxied chat streams over the shared agent WebSocket."""

    def __init__(self, ws_manager: WebSocketManager, memory_cache=None) -> None:
        self._ws = ws_manager
        self._memory_cache = memory_cache
        self._pending: dict[str, asyncio.Queue] = {}          # chat_id → event queue
        self._owner: dict[str, tuple[str, str]] = {}          # chat_id → (agent_id, host)

    # ── Frame ingress (called from the agents WebSocket route) ──────────────

    def deliver(self, chat_id: str, event: dict) -> bool:
        """Route one incoming `chat_event` to its waiting stream. Returns False
        when nothing is waiting (late frame after cancel/timeout — dropped)."""
        queue = self._pending.get(chat_id)
        if queue is None:
            return False
        queue.put_nowait(event)
        return True

    def fail_agent(self, agent_id: str, host: str | None = None) -> None:
        """A peer disconnected: terminate every chat it owned with an ERROR so
        no stream hangs until the idle timeout.

        Scoped to one machine when `host` is given. An agent attached from two
        machines has in-flight turns on each, and killing the server's turns
        because a laptop closed its lid would fail work that is still running
        perfectly well.
        """
        for chat_id, (owner, owner_host) in list(self._owner.items()):
            if owner != agent_id or (host is not None and owner_host != host):
                continue
            queue = self._pending.get(chat_id)
            if queue is not None:
                queue.put_nowait({
                    "type": "error",
                    "data": f"{agent_id.title()} disconnected mid-stream. "
                            "The task may be incomplete.",
                })

    async def _memory_block(self, context: AgentContext) -> str:
        """The owner's memory, as the orchestrator would assemble it.

        Best-effort by design: memory is context, not the turn. A recall that
        fails should cost the peer some background knowledge, never the job the
        owner actually asked for — so this returns "" and logs rather than
        raising into the stream.
        """
        if self._memory_cache is None or context.db is None:
            return ""
        try:
            return await recall_for_context(
                context.user_id, context.db, context.agent_id,
                cache=self._memory_cache,
            ) or ""
        except Exception as e:  # noqa: BLE001 — see docstring
            logger.warning(
                "external_memory_recall_failed",
                extra={"request_id": context.request_id, "error": str(e)},
            )
            return ""

    # ── Engine (same contract as AgentOrchestrator.run) ─────────────────────

    async def run(self, context: AgentContext) -> AsyncGenerator[SSEEvent, None]:
        """Proxy one chat turn to the external peer, yielding SSEEvents."""
        agent_id = context.agent_id
        cwd = context.extra.get("cwd")

        # Which machine runs this turn. The directory picks the peer, so the
        # owner selecting a folder is the whole interaction — and a folder no
        # connected peer covers is refused HERE rather than sent somewhere it
        # would silently succeed (app/core/peer_routing.py).
        decision = resolve(self._ws.peers(agent_id), cwd)
        if not decision.ok:
            logger.warning(
                "external_chat_unroutable",
                extra={"request_id": context.request_id, "agent_id": agent_id,
                       "cwd": cwd},
            )
            yield SSEEvent(
                type=SSEEventType.START, data="",
                session_id=context.session_id, request_id=context.request_id,
            )
            yield SSEEvent(
                type=SSEEventType.ERROR, data=decision.error,
                session_id=context.session_id, request_id=context.request_id,
            )
            return

        host = decision.host
        chat_id = str(uuid.uuid4())
        queue: asyncio.Queue = asyncio.Queue()
        self._pending[chat_id] = queue
        self._owner[chat_id] = (agent_id, host or "")
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
                "cwd": cwd,
                "system_prompt": None,
                # What the owner's in-process agents know about them. Until
                # this was sent, an external peer ran the owner's turns knowing
                # the conversation and nothing about the owner — every
                # preference and standing fact stopped at the socket. The peer
                # prepends it as a fragment and keeps its own identity, so this
                # is not a system prompt by another name (Rule 2 stands: the
                # orchestrator still owns that, and sends None above).
                "memory_block": await self._memory_block(context),
            }, host)
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

                data = event.get("data", "" if etype in ("chunk", "error") else {})
                if etype == "tool_result":
                    # Bridge the peer's Anthropic-native shape onto the {id,
                    # result} contract every consumer reads (see _EVENT_MAP note).
                    data = _normalize_tool_result(data)
                yield SSEEvent(
                    type=sse_type,
                    data=data,
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
                    }, host)
                except Exception:  # noqa: BLE001 — best-effort by design
                    pass
