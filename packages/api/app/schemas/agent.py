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
    created_at: datetime

    model_config = {"from_attributes": True}


class HousePartyState(BaseModel):
    engaged: bool


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
