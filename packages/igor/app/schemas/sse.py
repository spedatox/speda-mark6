# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

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
    # Arm the Skyfall Protocol: the client opens the full-screen countdown for
    # the named project. Carries the project, never a decision — the fire only
    # happens if the owner lets the clock run out, and the client is what runs
    # the clock. Nothing has been sent when this is emitted.
    SKYFALL_ARM = "skyfall_arm"
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
