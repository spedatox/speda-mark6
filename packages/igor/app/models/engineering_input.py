"""Authorized references to originals; extracted knowledge remains separate."""
from datetime import datetime, timezone
import uuid

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class EngineeringInput(Base):
    __tablename__ = "engineering_inputs"
    __table_args__ = (
        Index("ix_engineering_inputs_session", "session_id"),
        UniqueConstraint("session_id", "name", "content_hash", "media_type"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(128), default="")
    content_hash: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
