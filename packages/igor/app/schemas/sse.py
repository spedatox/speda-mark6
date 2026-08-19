from dataclasses import dataclass
from enum import Enum


class SSEEventType(str, Enum):
    START = "start"
    CHUNK = "chunk"
    TOOL = "tool"
    TOOL_RESULT = "tool_result"   # what a tool returned (for the disclosure panel)
    FILE = "file"     # a downloadable file Speda produced this turn
    PERMISSION_REQUEST = "permission_request"  # a peer's gate is asking the owner
    HOUSE_PARTY_AUTH = "house_party_auth"      # open the HPP authorization window
    LOCKDOWN_AUTH = "lockdown_auth"            # open the containment authorization window
    # A coding peer delegated part of its turn. Its own channel, never terminal:
    # a subagent finishing is not the turn finishing, and its report is not the
    # answer. Carries {id, agent, label, phase, ...} so a client can group
    # concurrent runs and fold each one away.
    SUBAGENT = "subagent"
    DONE = "done"
    ERROR = "error"


@dataclass
class SSEEvent:
    type: SSEEventType
    data: str | dict
    session_id: int
    request_id: str

    def to_json(self) -> str:
        """Serialise to a JSON string — used by the WebSocket handler."""
        import json

        return json.dumps(
            {
                "type": self.type.value,
                "data": self.data,
                "session_id": self.session_id,
                "request_id": self.request_id,
            }
        )

    def to_sse(self) -> str:
        """Serialise to the SSE wire format — used by the HTTP streaming handler."""
        return f"data: {self.to_json()}\n\n"
