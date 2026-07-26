from datetime import datetime

from pydantic import BaseModel


class AgentRegistration(BaseModel):
    type: str = "agent_register"
    agent_id: str
    agent_name: str
    domain: str
    capabilities: list[str]
    status: str = "online"
    model_preference: str = "haiku"


class AgentStatus(BaseModel):
    agent_id: str
    agent_name: str
    domain: str
    status: str
    last_seen: datetime | None
    capabilities: list[str]


class AgentCommEntry(BaseModel):
    """One inter-agent exchange, as shown in the comms tray."""

    id: int
    request_id: str
    from_agent: str
    to_agent: str
    kind: str
    protocol: str
    task: str
    result: str | None
    status: str
    duration_ms: int | None
    # The chat session this exchange was ordered from — the "room" it belongs to
    # in the group-chat view. None for traffic logged before the column existed.
    origin_session_id: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class HousePartyState(BaseModel):
    engaged: bool


class HousePartySet(BaseModel):
    """Engage/stand-down request. Engaging requires the owner's authorization
    passphrase (validated server-side); standing down needs none."""

    engaged: bool
    passphrase: str | None = None


class AgentModelInfo(BaseModel):
    """One agent's model allocation, as shown in the model routing UI."""

    agent_id: str
    name: str
    domain: str
    override: str | None      # owner's runtime pin; None = profile policy
    telegram_override: str | None = None
    default_main: str         # profile's interactive-grade model
    default_background: str   # profile's background-tier model


class AgentModelSet(BaseModel):
    agent_id: str
    model: str | None = None  # None/empty = clear the pin, back to profile policy


class AgentTelegramModelSet(BaseModel):
    agent_id: str
    model: str | None = None  # None/empty = clear the pin, use desktop model


class LegionModelInfo(BaseModel):
    """One Legion worker type's model allocation, for the Legion model UI.

    Legionnaires have no profile of their own — their model is derived from
    effort against whatever the DEPLOYING agent is running on, so there is no
    single "default model" to show. `effort` and `derived_from` describe that
    rule instead, and `override` is the owner's pin on top of it."""

    worker_id: str
    when_to_use: str
    effort: str
    derived_from: str          # human-readable description of the effort rule
    override: str | None       # owner's runtime pin; None = effort policy
    deployment_pin: str | None # LEGION_MODEL_OVERRIDE — overrides everything


class LegionModelSet(BaseModel):
    worker_id: str
    model: str | None = None  # None/empty = clear the pin, back to effort policy


class PendingAskEntry(BaseModel):
    """One irreversible operation an external peer is waiting to be told about.

    `action_key` is the exact command or path the peer's gate stopped. It is
    shown verbatim and never truncated — an owner approving a force-push needs
    to see which branch."""

    ask_id: str
    agent_id: str
    tool: str
    action_key: str
    reason: str
    job_id: str = ""
    chat_id: str | None = None
    seconds_left: float


class AskAnswer(BaseModel):
    """The owner's decision. `remember` records this exact action on the peer's
    allow-list so the same command stops asking; it never generalises to a
    pattern."""

    approved: bool
    remember: bool = False
    note: str = ""
