# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Addressable excerpts of source documents, never a second write path."""
from datetime import date
from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class MemoryPassage(Base):
    __tablename__ = "memory_passages"
    __table_args__ = (Index("ix_memory_passage_record", "user_id", "record_id", "retired"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    record_id: Mapped[str] = mapped_column(String(36))
    path: Mapped[str] = mapped_column(String(512))
    kind: Mapped[str] = mapped_column(String(32))
    text: Mapped[str] = mapped_column(Text)
    # A date mentioned by this entry, not a claim that an event was completed.
    mentioned_on: Mapped[date | None] = mapped_column(nullable=True)
    start_line: Mapped[int] = mapped_column()
    end_line: Mapped[int] = mapped_column()
    retired: Mapped[bool] = mapped_column(default=False)
