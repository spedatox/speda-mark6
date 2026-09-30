# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Typed, evidence-labelled links between durable memory and its sources."""

from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MemoryGraphEdge(Base):
    __tablename__ = "memory_graph_edges"
    __table_args__ = (
        UniqueConstraint("user_id", "source_ref", "target_ref", "relation_type",
                         name="uq_memory_graph_edge"),
        Index("ix_memory_graph_source", "user_id", "source_ref"),
        Index("ix_memory_graph_target", "user_id", "target_ref"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # Refs are type-qualified, e.g. observation:2336, message:9124,
    # record:<uuid>, revision:42, entity:person:Bedirhan, path:/memories/...
    source_ref: Mapped[str] = mapped_column(String(600))
    target_ref: Mapped[str] = mapped_column(String(600))
    relation_type: Mapped[str] = mapped_column(String(32))
    evidence_ref: Mapped[str | None] = mapped_column(String(600), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc)
    )
