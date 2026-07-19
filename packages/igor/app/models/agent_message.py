from datetime import datetime

from sqlalchemy import Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AgentMessage(Base):
    """
    One inter-agent exchange: an agent (or the House Party broadcast) dispatched
    a task to another agent and got a result back. Written by
    app/core/dispatch.py and read by GET /agents/comms for the comms tray in the
    UI. Rows are append-only telemetry — they are never load-bearing for the
    dispatch itself, so a failed write must never fail a dispatch.
    """

    __tablename__ = "agent_messages"
    __table_args__ = (Index("ix_agent_messages_created", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[str] = mapped_column(String(36))          # root request that spawned it
    from_agent: Mapped[str] = mapped_column(String(64))
    to_agent: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16), default="dispatch")    # dispatch | broadcast
    protocol: Mapped[str] = mapped_column(String(24), default="direct")  # direct | house_party
    task: Mapped[str] = mapped_column(Text)
    result: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    # running | ok | error | timeout | offline | refused
    status: Mapped[str] = mapped_column(String(16), default="running")
    duration_ms: Mapped[int | None] = mapped_column(nullable=True, default=None)
    session_id: Mapped[int | None] = mapped_column(nullable=True, default=None)  # target's session
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
