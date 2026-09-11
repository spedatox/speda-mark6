# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class FinanceIdentity(Base):
    __tablename__ = "finance_identities"
    __table_args__ = (UniqueConstraint("user_id", "record_id"), UniqueConstraint("user_id", "event_key"))
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    record_id: Mapped[str] = mapped_column(String(255))
    event_key: Mapped[str] = mapped_column(String(1024))
