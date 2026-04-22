from dataclasses import dataclass
from enum import Enum


class SSEEventType(str, Enum):
    START = "start"
    CHUNK = "chunk"
    TOOL = "tool"
    DONE = "done"
    ERROR = "error"


@dataclass
class SSEEvent:
    type: SSEEventType
    data: str | dict
    session_id: int
    request_id: str

    def to_sse(self) -> str:
        """Serialise to the SSE wire format consumed by Flutter."""
        import json

        payload = json.dumps(
            {
                "type": self.type.value,
                "data": self.data,
                "session_id": self.session_id,
                "request_id": self.request_id,
            }
        )
        return f"data: {payload}\n\n"
