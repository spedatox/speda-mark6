# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Solus's exact ledger. Prototype tables remain untouched for lossless migration."""
from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey,
    ForeignKeyConstraint, Integer, JSON, String, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


def uid() -> str:
    return str(uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


class SolusBook(Base):
    __tablename__ = "solus_books"
    __table_args__ = (UniqueConstraint("id", "user_id"), UniqueConstraint("user_id", "name"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(16))
    currency: Mapped[str] = mapped_column(String(3))
    timezone: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer, default=0)
    closed_through: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SolusAccount(Base):
    __tablename__ = "solus_accounts"
    __table_args__ = (
        UniqueConstraint("id", "book_id"), UniqueConstraint("book_id", "name", "currency"),
        CheckConstraint("account_type IN ('asset','liability','equity','income','expense')"),
        CheckConstraint("scale BETWEEN 0 AND 8"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    book_id: Mapped[str] = mapped_column(ForeignKey("solus_books.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    account_type: Mapped[str] = mapped_column(String(16))
    currency: Mapped[str] = mapped_column(String(3))
    scale: Mapped[int] = mapped_column(Integer)
    closed: Mapped[bool] = mapped_column(Boolean, default=False)
    report_role: Mapped[str] = mapped_column(String(16), default="financial")
    report_note: Mapped[str | None] = mapped_column(String(1000))


class SolusCardCalendar(Base):
    __tablename__ = "solus_card_calendars"
    __table_args__ = (
        ForeignKeyConstraint(["account_id", "book_id"], ["solus_accounts.id", "solus_accounts.book_id"]),
        CheckConstraint("closing_day BETWEEN 1 AND 31"),
        CheckConstraint("(due_day IS NOT NULL AND due_offset IS NULL AND due_day BETWEEN 1 AND 31) OR (due_day IS NULL AND due_offset BETWEEN 1 AND 60)"),
    )
    account_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    book_id: Mapped[str] = mapped_column(String(36), index=True)
    closing_day: Mapped[int] = mapped_column(Integer)
    due_day: Mapped[int | None] = mapped_column(Integer)
    due_offset: Mapped[int | None] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, default=1)
    history: Mapped[list] = mapped_column(JSON, default=list)


class SolusCardStatement(Base):
    __tablename__ = "solus_card_statements"
    __table_args__ = (
        UniqueConstraint("account_id", "period"),
        ForeignKeyConstraint(["account_id", "book_id"], ["solus_accounts.id", "solus_accounts.book_id"]),
        CheckConstraint("closes_on > opens_after AND due_on >= closes_on"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    book_id: Mapped[str] = mapped_column(String(36), index=True)
    account_id: Mapped[str] = mapped_column(String(36))
    period: Mapped[str] = mapped_column(String(7))
    opens_after: Mapped[date] = mapped_column(Date)
    closes_on: Mapped[date] = mapped_column(Date)
    due_on: Mapped[date] = mapped_column(Date)
    stated_balance_minor: Mapped[int | None] = mapped_column(BigInteger)
    minimum_due_minor: Mapped[int | None] = mapped_column(BigInteger)
    event_ids: Mapped[list] = mapped_column(JSON, default=list)
    evidence: Mapped[list] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer, default=1)


class SolusEvent(Base):
    __tablename__ = "solus_events"
    __table_args__ = (
        UniqueConstraint("id", "book_id"),
        UniqueConstraint("book_id", "occurrence_key"),
        UniqueConstraint("reverses_id"),
        ForeignKeyConstraint(["book_id", "user_id"], ["solus_books.id", "solus_books.user_id"]),
        ForeignKeyConstraint(["reverses_id", "book_id"], ["solus_events.id", "solus_events.book_id"]),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    book_id: Mapped[str] = mapped_column(String(36), index=True)
    occurrence_key: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(32))
    description: Mapped[str] = mapped_column(String(500))
    occurred_on: Mapped[date] = mapped_column(Date, index=True)
    posted: Mapped[bool] = mapped_column(Boolean, default=False)
    reverses_id: Mapped[str | None] = mapped_column(String(36))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence: Mapped[list] = mapped_column(JSON)
    actor: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SolusPosting(Base):
    __tablename__ = "solus_postings"
    __table_args__ = (
        ForeignKeyConstraint(["event_id", "book_id"], ["solus_events.id", "solus_events.book_id"]),
        ForeignKeyConstraint(["account_id", "book_id"], ["solus_accounts.id", "solus_accounts.book_id"]),
        CheckConstraint("amount_minor != 0 AND amount_minor BETWEEN -1000000000000000 AND 1000000000000000"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[str] = mapped_column(String(36), index=True)
    event_id: Mapped[str] = mapped_column(String(36), index=True)
    account_id: Mapped[str] = mapped_column(String(36), index=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger)


class SolusCommand(Base):
    __tablename__ = "solus_commands"
    __table_args__ = (UniqueConstraint("user_id", "command_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    command_key: Mapped[str] = mapped_column(String(200))
    payload_hash: Mapped[str] = mapped_column(String(64))
    receipt: Mapped[dict] = mapped_column(JSON)
    payload: Mapped[dict] = mapped_column(JSON)
    actor: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SolusScheduleItem(Base):
    __tablename__ = "solus_schedule_items"
    __table_args__ = (
        UniqueConstraint("event_id", "sequence"), UniqueConstraint("id", "book_id"),
        ForeignKeyConstraint(["event_id", "book_id"], ["solus_events.id", "solus_events.book_id"]),
        CheckConstraint("amount_minor >= 0"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    book_id: Mapped[str] = mapped_column(String(36), index=True)
    event_id: Mapped[str] = mapped_column(String(36), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    due_on: Mapped[date] = mapped_column(Date, index=True)
    statement_on: Mapped[date | None] = mapped_column(Date, index=True)
    currency: Mapped[str] = mapped_column(String(3))
    scale: Mapped[int] = mapped_column(Integer)
    amount_minor: Mapped[int] = mapped_column(BigInteger)


class SolusSettlement(Base):
    __tablename__ = "solus_settlements"
    __table_args__ = (
        ForeignKeyConstraint(["payment_id", "book_id"], ["solus_events.id", "solus_events.book_id"]),
        ForeignKeyConstraint(["item_id", "book_id"], ["solus_schedule_items.id", "solus_schedule_items.book_id"]),
        UniqueConstraint("payment_id", "item_id"), CheckConstraint("amount_minor > 0"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[str] = mapped_column(String(36), index=True)
    payment_id: Mapped[str] = mapped_column(String(36))
    item_id: Mapped[str] = mapped_column(String(36), index=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger)


class SolusPlan(Base):
    __tablename__ = "solus_plans"
    __table_args__ = (UniqueConstraint("book_id", "plan_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    book_id: Mapped[str] = mapped_column(ForeignKey("solus_books.id"), index=True)
    plan_key: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20))
    version: Mapped[int] = mapped_column(Integer, default=1)
    content: Mapped[dict] = mapped_column(JSON)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class SolusImport(Base):
    __tablename__ = "solus_imports"
    __table_args__ = (UniqueConstraint("book_id", "source_hash"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    book_id: Mapped[str] = mapped_column(ForeignKey("solus_books.id"), index=True)
    source_hash: Mapped[str] = mapped_column(String(64))
    original: Mapped[str] = mapped_column(String)
    rows: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SolusReconciliation(Base):
    __tablename__ = "solus_reconciliations"
    __table_args__ = (ForeignKeyConstraint(["account_id", "book_id"], ["solus_accounts.id", "solus_accounts.book_id"]),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    book_id: Mapped[str] = mapped_column(String(36), index=True)
    account_id: Mapped[str] = mapped_column(String(36), index=True)
    opens_after: Mapped[date] = mapped_column(Date)
    closes_on: Mapped[date] = mapped_column(Date)
    source_hash: Mapped[str] = mapped_column(String(64))
    original: Mapped[str] = mapped_column(String)
    matches: Mapped[list] = mapped_column(JSON)
    basis_hash: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SolusLot(Base):
    __tablename__ = "solus_lots"
    __table_args__ = (
        UniqueConstraint('id','book_id'),
        ForeignKeyConstraint(['event_id','book_id'],['solus_events.id','solus_events.book_id']),
        ForeignKeyConstraint(['account_id','book_id'],['solus_accounts.id','solus_accounts.book_id']),
        CheckConstraint('quantity > 0 AND cost_minor > 0 AND unit_scale BETWEEN 0 AND 8'),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    book_id: Mapped[str] = mapped_column(String(36), index=True)
    event_id: Mapped[str] = mapped_column(String(36))
    account_id: Mapped[str] = mapped_column(String(36))
    instrument: Mapped[str] = mapped_column(String(120))
    quantity: Mapped[int] = mapped_column(BigInteger)
    unit_scale: Mapped[int] = mapped_column(Integer)
    cost_minor: Mapped[int] = mapped_column(BigInteger)


class SolusDisposal(Base):
    __tablename__ = "solus_disposals"
    __table_args__ = (
        ForeignKeyConstraint(['event_id','book_id'],['solus_events.id','solus_events.book_id']),
        ForeignKeyConstraint(['lot_id','book_id'],['solus_lots.id','solus_lots.book_id']),
        CheckConstraint('quantity > 0 AND cost_minor >= 0'),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[str] = mapped_column(String(36), index=True)
    event_id: Mapped[str] = mapped_column(String(36))
    lot_id: Mapped[str] = mapped_column(String(36), index=True)
    quantity: Mapped[int] = mapped_column(BigInteger)
    cost_minor: Mapped[int] = mapped_column(BigInteger)


class SolusValuation(Base):
    __tablename__ = "solus_valuations"
    __table_args__ = (ForeignKeyConstraint(['account_id','book_id'],['solus_accounts.id','solus_accounts.book_id']),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    book_id: Mapped[str] = mapped_column(String(36), index=True)
    account_id: Mapped[str] = mapped_column(String(36))
    instrument: Mapped[str] = mapped_column(String(120))
    as_of: Mapped[date] = mapped_column(Date)
    quantity: Mapped[int] = mapped_column(BigInteger)
    unit_scale: Mapped[int] = mapped_column(Integer)
    value_minor: Mapped[int] = mapped_column(BigInteger)
    cost_minor: Mapped[int] = mapped_column(BigInteger)
    evidence: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SolusBrowserSession(Base):
    __tablename__ = "solus_browser_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    csrf_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    credential_version: Mapped[int] = mapped_column(Integer, default=0)


class SolusSetupToken(Base):
    __tablename__ = "solus_setup_tokens"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    redeemed: Mapped[bool] = mapped_column(Boolean, default=False)


class SolusCredential(Base):
    __tablename__ = "solus_credentials"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SolusAuthAttempt(Base):
    __tablename__ = "solus_auth_attempts"
    bucket: Mapped[str] = mapped_column(String(64), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    window_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
