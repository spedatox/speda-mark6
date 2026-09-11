# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
from datetime import datetime, timezone
from sqlalchemy import ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class MemoryWriteReceipt(Base):
    __tablename__ = "memory_write_receipts"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    path: Mapped[str] = mapped_column(String(512), index=True)
    author: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[str] = mapped_column(String(64), default="")
    before_hash: Mapped[str] = mapped_column(String(64))
    after_hash: Mapped[str] = mapped_column(String(64))
    evidence: Mapped[list] = mapped_column(JSON)
    rationale: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
