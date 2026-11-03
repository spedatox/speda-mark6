"""Original source intake and disposable, revision-fenced serving projections."""
from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Index, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MemoryChange(Base):
    __tablename__ = "memory_changes"
    __table_args__ = (
        UniqueConstraint("user_id", "source_key", name="uq_memory_change_source"),
        Index("ix_memory_changes_pending", "user_id", "outcome", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    source_key: Mapped[str] = mapped_column(String(512))
    source_role: Mapped[str] = mapped_column(String(32))
    session_id: Mapped[int | None] = mapped_column(ForeignKey("sessions.id"), nullable=True)
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id"), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    routing_text: Mapped[str] = mapped_column(Text)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)
    outcome: Mapped[str] = mapped_column(String(32), default="pending")
    capture_offset: Mapped[int] = mapped_column(default=0)
    attempts: Mapped[int] = mapped_column(default=0)
    claim_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    candidate_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    revoked: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))


class MemoryServingUnit(Base):
    __tablename__ = "memory_serving_units"
    __table_args__ = (
        UniqueConstraint("user_id", "stable_key", name="uq_memory_serving_key"),
        Index("ix_memory_serving_owner_kind", "user_id", "kind"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    stable_key: Mapped[str] = mapped_column(String(256))
    kind: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(256))
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    sections: Mapped[list] = mapped_column(JSON)
    dependencies: Mapped[list] = mapped_column(JSON)
    routing_text: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(default=1)
    compiler_version: Mapped[int] = mapped_column(default=1)
    published_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))


class MemoryServingState(Base):
    __tablename__ = "memory_serving_state"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    epoch: Mapped[int] = mapped_column(default=0)
    schema_version: Mapped[int] = mapped_column(default=1)
