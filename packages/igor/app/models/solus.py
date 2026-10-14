# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

from decimal import Decimal
from datetime import date, datetime, timezone
from sqlalchemy import ForeignKey, String, Date, Numeric, Integer, DateTime, Enum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base

class SolusTransaction(Base):
    __tablename__ = "solus_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    entity: Mapped[str] = mapped_column(String(255))
    transaction_type: Mapped[str] = mapped_column(String(32)) # e.g. expense, income, transfer
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="TRY")
    date: Mapped[date] = mapped_column(Date)
    installment_count: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    installments: Mapped[list["SolusInstallment"]] = relationship(
        "SolusInstallment", back_populates="transaction", cascade="all, delete-orphan"
    )

class SolusInstallment(Base):
    __tablename__ = "solus_installments"

    id: Mapped[int] = mapped_column(primary_key=True)
    transaction_id: Mapped[int] = mapped_column(ForeignKey("solus_transactions.id"), index=True)
    month_period: Mapped[str] = mapped_column(String(7), index=True) # e.g. '2026-10'
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))

    transaction: Mapped["SolusTransaction"] = relationship("SolusTransaction", back_populates="installments")
