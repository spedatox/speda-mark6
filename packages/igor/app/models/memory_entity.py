# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

from datetime import datetime, timezone

from sqlalchemy import ForeignKey, String, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MemoryEntity(Base):
    """
    Core identifiable subject of facts in the Monthly Memory Architecture.
    """

    __tablename__ = "memory_entities"
    __table_args__ = (
        UniqueConstraint("user_id", "category", "canonical_name", name="uq_memory_entity_user_cat_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    category: Mapped[str] = mapped_column(String(32))
    entity_type: Mapped[str] = mapped_column(String(32))
    canonical_name: Mapped[str] = mapped_column(String(256))
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
