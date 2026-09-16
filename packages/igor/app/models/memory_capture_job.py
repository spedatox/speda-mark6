# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import ForeignKey, String, Text, JSON, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MemoryCaptureJob(Base):
    """
    Queue for processing raw observations into structured memory records.
    """

    __tablename__ = "memory_capture_jobs"
    __table_args__ = (
        UniqueConstraint("user_id", "source_id", name="uq_capture_job_user_source"),
        Index("ix_capture_job_state", "user_id", "state"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    source_id: Mapped[str] = mapped_column(String(128))
    candidate_id: Mapped[str] = mapped_column(String(36))
    original_evidence: Mapped[list] = mapped_column(JSON, default=list)
    kind: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    category_hint: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    state: Mapped[str] = mapped_column(String(20))
    attempts: Mapped[int] = mapped_column(default=0)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    committed_record_ids: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    source_timestamp: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    source_timezone: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
