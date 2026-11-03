from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas.memory_runtime import MemoryBudget, MemoryPacket
from app.schemas.llm import EffectivePolicy, ThinkingRequest


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
    memory_budget: MemoryBudget = field(default_factory=MemoryBudget)
    memory_packet: MemoryPacket | None = None
    memory_source_id: int | None = None
    agent_id: str = "speda"
    requested_model: str | None = None
    thinking_request: ThinkingRequest | None = None
    effective_policy: EffectivePolicy | None = None
    task_role: str = "interactive"
    room_id: str | None = None
    run_id: str | None = None
    step_id: str | None = None
    parent_step_id: str | None = None
    project_id: str | None = None
    persona_snapshot: dict | None = None
    allowed_tools: frozenset[str] | None = None
    coordination: object | None = None
    budget_handle: object | None = None
    model_call_index: int = 0
    tool_call_id: str | None = None
    prompt_datetime: str | None = None
    output_token_cap: int | None = None
