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
