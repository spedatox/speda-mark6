# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import String, JSON, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MemoryMigrationRun(Base):
    """
    Tracks state and progress of large-scale schema migrations.
    """

    __tablename__ = "memory_migration_runs"
    __table_args__ = (
        Index("ix_migration_run_plan", "plan_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    plan_id: Mapped[str] = mapped_column(String(128))
    plan_hash: Mapped[str] = mapped_column(String(64))
    source_fingerprint: Mapped[str] = mapped_column(String(64))
    phase: Mapped[str] = mapped_column(String(32))
    schema_version_from: Mapped[int] = mapped_column()
    schema_version_to: Mapped[int] = mapped_column()
    total_sources: Mapped[int] = mapped_column(default=0)
    committed_count: Mapped[int] = mapped_column(default=0)
    commit_watermark: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    reconciliation_report: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
