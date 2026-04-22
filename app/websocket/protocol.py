"""
WebSocket message type definitions for agent-backend communication.
These are the message types used by the Superior Six agents connecting to the backend.
Not used for Flutter user sessions — those use SSE (POST /chat) or WS /ws.
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel


class AgentMessageType(str, Enum):
    # Agent → Backend
    REGISTER = "agent_register"
    HEARTBEAT = "heartbeat"
    TASK_RESULT = "task_result"
    STATUS_UPDATE = "status_update"

    # Backend → Agent
    TASK_DISPATCH = "task_dispatch"
    ACKNOWLEDGE = "acknowledge"
    SHUTDOWN = "shutdown"


class AgentMessage(BaseModel):
    type: AgentMessageType
    agent_id: str
    payload: dict[str, Any] = {}


class TaskDispatch(BaseModel):
    type: AgentMessageType = AgentMessageType.TASK_DISPATCH
    agent_id: str
    task_id: str
    description: str
    prompt: str
    output_mode: str = "silent"
