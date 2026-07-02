"""
WebSocket message type definitions for agent-backend communication.
These are the message types used by external peer agents (Optimus) connecting
to the backend. Not used for Flutter user sessions — those use SSE (POST /chat)
or WS /ws.

Two conversation shapes ride the same socket:

- Task dispatch (fire-and-await): the backend sends `task_dispatch`, the peer
  answers once with a correlated `task_result`. Used by AgentDispatcher when
  another agent delegates work to the peer.
- Chat proxy (streamed): the backend sends `chat_request` for an interactive
  user session, the peer streams `chat_event` frames (chunk/tool/tool_result/
  done/error — the same vocabulary as app/schemas/sse.py) correlated by
  chat_id, until a terminal done/error. `chat_cancel` aborts a run in flight.

Correlation ids (task_id / chat_id) make concurrent conversations safe over
one socket.
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class AgentMessageType(str, Enum):
    # Agent → Backend
    REGISTER = "agent_register"
    HEARTBEAT = "heartbeat"
    TASK_RESULT = "task_result"
    STATUS_UPDATE = "status_update"
    CHAT_EVENT = "chat_event"

    # Backend → Agent
    TASK_DISPATCH = "task_dispatch"
    CHAT_REQUEST = "chat_request"
    CHAT_CANCEL = "chat_cancel"
    ACKNOWLEDGE = "acknowledge"
    SHUTDOWN = "shutdown"


class AgentMessage(BaseModel):
    type: AgentMessageType
    agent_id: str
    payload: dict[str, Any] = {}


class TaskDispatch(BaseModel):
    """Backend → peer: run a delegated task and answer with `task_result`.
    This is the canonical wire shape — AgentDispatcher._run_external sends it."""

    type: AgentMessageType = AgentMessageType.TASK_DISPATCH
    task_id: str
    from_agent: str = Field(alias="from")
    task: str
    cwd: str | None = None
    # Peer-side ceiling applies: a frame can request a stricter mode
    # (e.g. "plan") but can never escalate past the peer's configured mode.
    permission_mode: str | None = None

    model_config = {"populate_by_name": True}


class TaskResult(BaseModel):
    """Peer → backend: the single answer to a `task_dispatch`."""

    type: AgentMessageType = AgentMessageType.TASK_RESULT
    agent_id: str
    task_id: str
    result: str
    status: str = "ok"  # "ok" | "error"


class ChatRequestFrame(BaseModel):
    """Backend → peer: run one interactive chat turn. The peer is stateless —
    the full Anthropic-format history is sent every turn (the backend DB is the
    source of truth; peer-side prompt caching still applies)."""

    type: AgentMessageType = AgentMessageType.CHAT_REQUEST
    chat_id: str
    session_id: int
    request_id: str
    # Anthropic messages format, ending with the user turn to answer.
    history: list[dict[str, Any]]
    # None → the peer picks its own default model (nothing model-related is
    # hardcoded backend-side for external runs). Otherwise an opaque model ref
    # the peer resolves ("claude-sonnet-4-6", "ollama:qwen3:14b", …).
    model: str | None = None
    cwd: str | None = None
    system_prompt: str | None = None


class ChatEventFrame(BaseModel):
    """Peer → backend: one streamed event of a proxied chat. `event` mirrors
    the SSE vocabulary: {"type": "chunk"|"tool"|"tool_result"|"done"|"error",
    "data": …} so ExternalAgentProxy can re-wrap it 1:1 as an SSEEvent."""

    type: AgentMessageType = AgentMessageType.CHAT_EVENT
    chat_id: str
    event: dict[str, Any]


class ChatCancel(BaseModel):
    """Backend → peer: abort a proxied chat that is still streaming."""

    type: AgentMessageType = AgentMessageType.CHAT_CANCEL
    chat_id: str
