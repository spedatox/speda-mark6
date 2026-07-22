from dataclasses import dataclass
from enum import Enum


class SSEEventType(str, Enum):
    START = "start"
    CHUNK = "chunk"
    TOOL = "tool"
    TOOL_RESULT = "tool_result"   # what a tool returned (for the disclosure panel)
    FILE = "file"     # a downloadable file SPEDA produced this turn
    PERMISSION_REQUEST = "permission_request"  # a peer's gate is asking the owner
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
