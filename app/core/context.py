from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class AgentContext:
    """
    Single source of truth for all request state (CLAUDE.md Rule 3).
    Every module that needs user, session, DB, model, or timezone info receives this.
    No module-level globals. No ad-hoc dicts.
    """

    user_id: int
    session_id: int
    request_id: str                          # UUID — in every log line and SSE event
    triggered_by: Literal["user", "n8n", "agent"]
    trigger_payload: dict
    output_mode: Literal["respond", "push", "silent"]
    model: str                               # Set by profile.allocate_model() — never hardcoded
    system_prompt: str                       # Built by AgentOrchestrator.build_system_prompt()
    conversation_history: list[dict]         # Anthropic messages format
    db: AsyncSession
    timezone: str = "UTC"
    extra: dict = field(default_factory=dict)  # Arbitrary per-request metadata
