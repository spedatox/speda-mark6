# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Immutable originals, separate from current knowledge and model context."""
from datetime import datetime, timezone
from sqlalchemy import ForeignKey, Index, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class MemorySource(Base):
    __tablename__ = "memory_sources"
    __table_args__ = (
        UniqueConstraint("user_id", "source_key", name="uq_memory_source_key"),
        Index("ix_memory_source_path", "user_id", "original_path"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    source_key: Mapped[str] = mapped_column(String(256))
    original_path: Mapped[str] = mapped_column(String(512))
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    classification: Mapped[str] = mapped_column(String(32))
    # Full original row, including original IDs/timestamps. Never a summary.
    original_row: Mapped[dict] = mapped_column(JSON)
    migration_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))


class MemoryIssue(Base):
    """Unresolved discrepancies; candidates never become facts automatically."""
    __tablename__ = "memory_issues"
    __table_args__ = (UniqueConstraint("user_id", "issue_key", name="uq_memory_issue_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    issue_key: Mapped[str] = mapped_column(String(256))
    kind: Mapped[str] = mapped_column(String(32))
    refs: Mapped[list] = mapped_column(JSON)
    detail: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="open")
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
