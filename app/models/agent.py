from datetime import datetime

from sqlalchemy import ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AgentRecord(Base):
    __tablename__ = "agent_registry"

    agent_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default="offline")  # online | offline | error
    last_seen: Mapped[datetime | None] = mapped_column(nullable=True, default=None)
    capabilities: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
    current_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("sessions.id"), nullable=True, default=None
    )
