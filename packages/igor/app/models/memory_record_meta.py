# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import ForeignKey, String, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MemoryRecordMeta(Base):
    """
    Metadata index for individual memory records.
    """

    __tablename__ = "memory_record_meta"
    __table_args__ = (
        UniqueConstraint("user_id", "record_id", name="uq_record_meta_user_record"),
        Index("ix_record_meta_cat_period", "user_id", "category", "period"),
        Index("ix_record_meta_entity", "user_id", "entity_id", "edition_seq"),
        Index("ix_record_meta_occurrence", "user_id", "occurrence_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    memory_file_id: Mapped[int] = mapped_column(ForeignKey("memory_files.id"), unique=True, index=True)
    record_id: Mapped[str] = mapped_column(String(36))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    entity_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    occurrence_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    category: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(32))
    period: Mapped[str] = mapped_column(String(7))
    period_basis: Mapped[str] = mapped_column(String(20))
    occurred_on: Mapped[Optional[date]] = mapped_column(nullable=True)
    occurred_until: Mapped[Optional[date]] = mapped_column(nullable=True)
    effective_from: Mapped[Optional[date]] = mapped_column(nullable=True)
    effective_until: Mapped[Optional[date]] = mapped_column(nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
    source_recorded_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    date_precision: Mapped[str] = mapped_column(String(10))
    source_timezone: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    edition_seq: Mapped[Optional[int]] = mapped_column(nullable=True)
    schema_version: Mapped[int] = mapped_column(default=1)
    version: Mapped[int] = mapped_column(default=1)
    content_hash: Mapped[str] = mapped_column(String(64))
