# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MemoryPathAlias(Base):
    """
    Redirects for virtual filesystem paths that have been moved or migrated.
    """

    __tablename__ = "memory_path_aliases"
    __table_args__ = (
        UniqueConstraint("user_id", "old_path", name="uq_path_alias_user_path"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    old_path: Mapped[str] = mapped_column(String(512))
    target_record_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    target_entity_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    alias_type: Mapped[str] = mapped_column(String(32))
    migration_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
