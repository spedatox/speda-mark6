# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Append-only semantic review attestations bound to exact document bytes."""

from datetime import datetime, timezone
from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class MemoryReview(Base):
    __tablename__ = "memory_reviews"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    path: Mapped[str] = mapped_column(String(512), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    author: Mapped[str] = mapped_column(String(64))
    findings: Mapped[str] = mapped_column(Text, default="[]")
    rationale: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
