# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

from datetime import date
from typing import Annotated, Literal
from zoneinfo import ZoneInfo
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator
from app.services.solus.money import currency_scale, minor


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Evidence(Strict):
    ref: str = Field(min_length=1, max_length=1000)
    quote: str = Field(min_length=1, max_length=12000)


class Command(Strict):
    command_key: str = Field(min_length=8, max_length=200)
    evidence: list[Evidence] = Field(default_factory=list, max_length=20)


class BookCreate(Command):
    operation: Literal["book_create"]
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["personal", "business"]
    currency: str = "TRY"
    timezone: str = "Europe/Istanbul"

    @field_validator("currency")
    @classmethod
    def currency_valid(cls, value):
        currency_scale(value)
        return value

    @field_validator("timezone")
    @classmethod
    def timezone_valid(cls, value):
        try:
            ZoneInfo(value)
        except (ValueError, KeyError):
            raise ValueError("Use a valid IANA timezone.") from None
        return value


class Scoped(Command):
    book_id: str = Field(min_length=1, max_length=36)


class AccountCreate(Scoped):
    operation: Literal["account_create"]
    name: str = Field(min_length=1, max_length=120)
    account_type: Literal["asset", "liability", "equity", "income", "expense"]
    currency: str = "TRY"

    @field_validator("currency")
    @classmethod
    def currency_valid(cls, value):
        currency_scale(value)
        return value


class AccountReportPut(Scoped):
    operation: Literal["account_report_put"]
    account_id: str = Field(min_length=1, max_length=36)
    expected_role: Literal["financial", "review"]
    report_role: Literal["financial", "review"]
    note: str = Field(min_length=1, max_length=1000)


class Line(Strict):
    account_id: str = Field(min_length=1, max_length=36)
    side: Literal["debit", "credit"]
    amount: str = Field(min_length=1, max_length=32)


class Allocation(Strict):
    item_id: str = Field(min_length=1, max_length=36)
    amount: str = Field(min_length=1, max_length=32)


class Document(Strict):
    number: str | None = Field(default=None, max_length=200)
    party: str | None = Field(default=None, max_length=200)
    reference: str | None = Field(default=None, max_length=1000)
    tax_note: str | None = Field(default=None, max_length=2000)


class Post(Scoped):
    operation: Literal["post"]
    occurrence_key: str = Field(min_length=1, max_length=200)
    kind: Literal["expense", "income", "transfer", "card_purchase", "debt_payment", "loan_disbursement",
                  "refund", "invoice", "bill", "invoice_payment", "bill_payment", "credit_note", "opening", "journal", "fx", "reimbursement"]
    description: str = Field(min_length=1, max_length=500)
    occurred_on: date
    booked_on: date | None = None
    billed_on: date | None = None
    document: Document | None = None
    related_event_id: str | None = Field(default=None, min_length=1, max_length=36)
    lines: list[Line] = Field(min_length=2, max_length=100)
    evidence: list[Evidence] = Field(default_factory=list, max_length=20)
    first_due_on: date | None = None
    first_statement_on: date | None = None
    installments: int = Field(default=1, ge=1, le=600, strict=True)
    schedule_amount: str | None = Field(default=None, max_length=32)
    schedule_currency: str | None = None
    settlements: list[Allocation] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def schedule_valid(self):
        if self.billed_on and (self.billed_on < self.occurred_on or self.kind == "card_purchase"):
            raise ValueError("Billed date must follow the occurrence; scheduled card purchases use their schedules.")
        if self.first_due_on:
            if not self.schedule_amount or not self.schedule_currency:
                raise ValueError("Schedule needs an explicit amount, currency and first due date.")
            minor(self.schedule_amount, self.schedule_currency)
            if self.first_due_on < self.occurred_on:
                raise ValueError("First due date cannot precede the occurrence.")
        elif self.kind != "card_purchase" and (self.installments != 1 or self.schedule_amount or self.schedule_currency):
            raise ValueError("Supply first_due_on to create a schedule.")
        if self.first_statement_on and self.kind != "card_purchase":
            raise ValueError("first_statement_on belongs to a card purchase.")
        if self.settlements and self.kind not in {"debt_payment", "invoice_payment", "bill_payment", "credit_note", "reimbursement"}:
            raise ValueError("Allocations belong to a payment operation.")
        return self


class Reverse(Scoped):
    operation: Literal["reverse"]
    event_id: str = Field(min_length=1, max_length=36)
    occurred_on: date
    reason: str = Field(min_length=1, max_length=500)
    evidence: list[Evidence] = Field(default_factory=list, max_length=20)


class ClosePeriod(Scoped):
    operation: Literal["close_period"]
    through: date | None
    expected_revision: int = Field(ge=0, strict=True)


class ImportCommit(Scoped):
    operation: Literal["import_commit"]
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    original: str = Field(min_length=1, max_length=1_000_000)
    source_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class ReviewedMatch(Strict):
    row_ref: str = Field(min_length=1, max_length=200)
    event_id: str = Field(min_length=1, max_length=36)


class Reconcile(Scoped):
    operation: Literal["reconcile"]
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    account_id: str = Field(min_length=1, max_length=36)
    opens_after: date
    closes_on: date
    opening_balance: str = Field(min_length=1, max_length=33)
    closing_balance: str = Field(min_length=1, max_length=33)
    original: str = Field(min_length=1, max_length=1_000_000)
    expected_revision: int = Field(ge=0, strict=True)
    source_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    matches: list[ReviewedMatch] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def valid(self):
        if self.closes_on <= self.opens_after:
            raise ValueError("Reconciliation needs a closing date after its prior close.")
        return self


class InvestmentBuy(Scoped):
    operation: Literal['investment_buy']
    occurrence_key: str = Field(min_length=1, max_length=200)
    occurred_on: date
    holding_account_id: str = Field(min_length=1, max_length=36)
    cash_account_id: str = Field(min_length=1, max_length=36)
    instrument: str = Field(min_length=1, max_length=120)
    quantity: str = Field(min_length=1, max_length=32)
    unit_scale: int = Field(default=8, ge=0, le=8, strict=True)
    cost: str = Field(min_length=1, max_length=32)
    fee: str = Field(default='0.00', max_length=32)
    fee_treatment: Literal['capitalize','expense'] = 'capitalize'
    fee_account_id: str | None = Field(default=None, max_length=36)


class InvestmentSell(Scoped):
    operation: Literal['investment_sell']
    occurrence_key: str = Field(min_length=1, max_length=200)
    occurred_on: date
    lot_id: str = Field(min_length=1, max_length=36)
    cash_account_id: str = Field(min_length=1, max_length=36)
    gain_account_id: str = Field(min_length=1, max_length=36)
    loss_account_id: str = Field(min_length=1, max_length=36)
    quantity: str = Field(min_length=1, max_length=32)
    proceeds: str = Field(min_length=1, max_length=32)
    fee: str = Field(default='0.00', max_length=32)
    fee_account_id: str | None = Field(default=None, max_length=36)


class ValuationPut(Scoped):
    operation: Literal['valuation_put']
    account_id: str = Field(min_length=1, max_length=36)
    instrument: str = Field(min_length=1, max_length=120)
    as_of: date
    value: str = Field(min_length=1, max_length=32)


class CardCalendarPut(Scoped):
    operation: Literal["card_calendar_put"]
    unbilled_account_id: str | None = Field(default=None, min_length=1, max_length=36)
    account_id: str = Field(min_length=1, max_length=36)
    closing_day: int = Field(ge=1, le=31, strict=True)
    due_day: int | None = Field(default=None, ge=1, le=31, strict=True)
    due_offset: int | None = Field(default=None, ge=1, le=60, strict=True)
    expected_version: int = Field(ge=0, strict=True)
    effective_period: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")

    @model_validator(mode="after")
    def valid(self):
        if (self.due_day is None) == (self.due_offset is None):
            raise ValueError("Supply exactly one of due_day or due_offset.")
        if self.effective_period:
            date.fromisoformat(self.effective_period + "-01")
        return self


class CardStatementPut(Scoped):
    operation: Literal["card_statement_put"]
    account_id: str = Field(min_length=1, max_length=36)
    event_ids: list[str] | None = Field(default=None, max_length=500)
    period: str = Field(pattern=r"^\d{4}-\d{2}$")
    opens_after: date
    closes_on: date
    due_on: date
    stated_balance: str | None = None
    minimum_due: str | None = None
    expected_version: int = Field(ge=0, strict=True)
    evidence: list[Evidence] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def valid(self):
        if self.closes_on <= self.opens_after or self.due_on < self.closes_on:
            raise ValueError("Statement must follow its prior close and be due on/after closing.")
        if self.closes_on.strftime("%Y-%m") != self.period:
            raise ValueError("Statement period is the closing month.")
        return self


class PlanPut(Scoped):
    operation: Literal["plan_put"]
    plan_key: str = Field(min_length=1, max_length=200)
    expected_version: int = Field(ge=0, strict=True)
    kind: Literal["budget", "recurring", "goal"]
    frequency: Literal["monthly", "weekly", "annual"] = "monthly"
    name: str = Field(min_length=1, max_length=120)
    account_id: str = Field(min_length=1, max_length=36)
    amount: str = Field(min_length=1, max_length=32)
    currency: str
    starts_on: date
    ends_on: date | None = None
    active: bool = True

    @model_validator(mode="after")
    def valid(self):
        minor(self.amount, self.currency)
        if self.ends_on and self.ends_on < self.starts_on:
            raise ValueError("End cannot precede start.")
        return self


SolusCommandInput = Annotated[BookCreate | AccountCreate | AccountReportPut | Post | Reverse | ClosePeriod | PlanPut | CardCalendarPut | CardStatementPut | ImportCommit | Reconcile | InvestmentBuy | InvestmentSell | ValuationPut, Field(discriminator="operation")]


def parse_command(payload: dict):
    return TypeAdapter(SolusCommandInput).validate_python(payload)


class SessionCreate(Strict):
    pass


class Login(Strict):
    password: str = Field(min_length=1, max_length=256)
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False, hide_input_in_errors=True)


class PasswordChange(Login):
    new_password: str = Field(min_length=12, max_length=256)


class PasswordSetup(Strict):
    new_password: str = Field(min_length=12, max_length=256)
    token: str | None = Field(default=None, max_length=200)
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False, hide_input_in_errors=True)
