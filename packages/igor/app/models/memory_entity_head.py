# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

from datetime import datetime, timezone

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MemoryEntityHead(Base):
    """
    Tracks the current active edition of an entity.
    """

    __tablename__ = "memory_entity_heads"
    __table_args__ = (
        UniqueConstraint("user_id", "entity_id", name="uq_entity_head_user_entity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    entity_id: Mapped[str] = mapped_column(String(36))
    current_edition_id: Mapped[str] = mapped_column(String(36))
    version: Mapped[int] = mapped_column(default=1)
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
