# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import ForeignKey, String, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MemoryRecordLink(Base):
    """
    Links between memory records representing relationships like supersedes or segment_of.
    """

    __tablename__ = "memory_record_links"
    __table_args__ = (
        Index("ix_record_link_source", "user_id", "source_record_id"),
        Index("ix_record_link_target", "user_id", "target_record_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    source_record_id: Mapped[str] = mapped_column(String(36))
    target_record_id: Mapped[str] = mapped_column(String(36))
    relation_type: Mapped[str] = mapped_column(String(32))
    source_anchor: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
