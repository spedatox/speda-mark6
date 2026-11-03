# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pure command effects. The memory-store coordinator owns commit and rollback."""
from collections import defaultdict
from datetime import date
from zoneinfo import ZoneInfo
from datetime import datetime

from sqlalchemy import select, update
from app.models.solus_ledger import (
    SolusBook, SolusAccount, SolusEvent, SolusPosting, SolusScheduleItem,
    SolusSettlement, SolusPlan, SolusCardCalendar, SolusCardStatement, SolusImport, SolusReconciliation,
)
from app.schemas.solus import BookCreate, AccountCreate, AccountReportPut, Post, Reverse, ClosePeriod, PlanPut, CardCalendarPut, CardStatementPut, ImportCommit, Reconcile, InvestmentBuy, InvestmentSell, ValuationPut
from app.services.solus.money import currency_scale, minor, allocate, monthly_dates, money
from app.services.solus.calendars import cycle_for_booking, cycle_for_close, shift_month, day_in_month


class SolusError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


async def book_for(db, user_id, book_id):
    book = await db.get(SolusBook, book_id)
    if not book or book.user_id != user_id:
        raise SolusError("Book not found.", 404)
    return book


async def account_for(db, book_id, account_id):
    account = await db.get(SolusAccount, account_id)
    if not account or account.book_id != book_id:
        raise SolusError("Account not found in this book.", 404)
    if account.closed:
        raise SolusError("Account is closed.", 409)
    return account


async def cached_read(cache, key, reader):
    if cache is None:
        return await reader()
    if key not in cache:
        cache[key] = await reader()
    return cache[key]


async def calendar_rule(db, account_id, period, cache=None):
    calendar = await cached_read(cache, ("calendar", account_id), lambda: db.get(SolusCardCalendar, account_id))
    if not calendar:
        raise SolusError("Configure this card's statement closing and due dates first.")
    versions = [v for v in calendar.history if v["effective_period"] <= period]
    if not versions:
        raise SolusError("Card has no calendar for this period.")
    return max(versions, key=lambda v: (v["effective_period"], v["version"]))


async def card_cycle(db, account_id, close, cache=None):
    async def statement_for(period):
        return await db.scalar(select(SolusCardStatement).where(
            SolusCardStatement.account_id == account_id, SolusCardStatement.period == period))
    override = await cached_read(cache, ("statement", account_id, close.strftime("%Y-%m")),
        lambda: statement_for(close.strftime("%Y-%m")))
    if override:
        return override.opens_after, override.closes_on, override.due_on
    rule = await calendar_rule(db, account_id, close.strftime("%Y-%m"), cache)
    close = day_in_month(close.year, close.month, rule["closing_day"])
    previous, close, due = cycle_for_close(close, rule["closing_day"], rule["due_day"], rule["due_offset"])
    previous_rule = await calendar_rule(db, account_id, previous.strftime("%Y-%m"), cache)
    previous = day_in_month(previous.year, previous.month, previous_rule["closing_day"])
    actual_previous = await cached_read(cache, ("statement", account_id, previous.strftime("%Y-%m")),
        lambda: statement_for(previous.strftime("%Y-%m")))
    return actual_previous.closes_on if actual_previous else previous, close, due


async def booking_cycle(db, account_id, booked_on, cache=None):
    calendar = await cached_read(cache, ("calendar", account_id), lambda: db.get(SolusCardCalendar, account_id))
    if not calendar:
        raise SolusError("Configure this card's statement closing and due dates first.")
    # Actual statement boundaries outrank the default cutoff, including shifted closes.
    async def load_overrides():
        return (await db.execute(select(SolusCardStatement).where(SolusCardStatement.account_id == account_id))).scalars().all()
    supplied = await cached_read(cache, ("all_statements", account_id), load_overrides)
    overrides = [s for s in supplied if s.opens_after < booked_on <= s.closes_on]
    if len(overrides) > 1:
        raise SolusError("Statement boundaries overlap; reconcile the calendar.", 409)
    if overrides:
        item = overrides[0]
        return item.opens_after, item.closes_on, item.due_on
    cycle = await card_cycle(db, account_id, booked_on, cache)
    if not cycle[0] < booked_on <= cycle[1]:
        close = shift_month(booked_on, 1, 1)
        cycle = await card_cycle(db, account_id, close, cache)
    if not cycle[0] < booked_on <= cycle[1]:
        raise SolusError("Booking date does not fit the configured statement boundaries.", 409)
    return cycle


async def effective_settled(db, item_id):
    # Reversing a payment releases its allocations without deleting their history.
    reversed_ids = select(SolusEvent.reverses_id).where(SolusEvent.reverses_id.is_not(None), SolusEvent.posted.is_(True))
    amounts = (await db.execute(select(SolusSettlement.amount_minor).join(
        SolusEvent, SolusEvent.id == SolusSettlement.payment_id,
    ).where(SolusSettlement.item_id == item_id, SolusEvent.posted.is_(True),
            SolusEvent.id.not_in(reversed_ids)))).scalars().all()
    return sum(amounts)


async def effective_dates(db, item, cache=None, account_id=None):
    if item.statement_on:
        if account_id is None:
            account_id = await cached_read(cache, ("card", item.event_id), lambda: db.scalar(select(SolusPosting.account_id).where(SolusPosting.event_id == item.event_id,
                SolusPosting.amount_minor < 0).join(SolusAccount, SolusAccount.id == SolusPosting.account_id).where(SolusAccount.account_type == "liability")))
        original = await cached_read(cache, ("event", item.event_id), lambda: db.get(SolusEvent, item.event_id))
        booking = date.fromisoformat(original.details.get("booked_on", original.occurred_on.isoformat()))
        cycle = await cached_read(cache, ("booking_cycle", account_id, booking),
            lambda: booking_cycle(db, account_id, booking, cache))
        first_close = cycle[1]
        deferred = original.details.get("first_statement_on")
        if "first_statement_on" not in original.details:
            # Older posted rows are immutable. Recover an explicit deferral
            # from their original command, never rewrite the stored event.
            from app.models.solus_ledger import SolusCommand
            async def source_payload():
                return await db.scalar(select(SolusCommand.payload).where(SolusCommand.user_id == original.user_id,
                    SolusCommand.receipt["event_id"].as_string() == original.id))
            source = await cached_read(cache, ("original_command", original.id), source_payload)
            deferred = (source or {}).get("first_statement_on")
        if deferred and deferred[:7] > first_close.strftime("%Y-%m"):
            first_close = date.fromisoformat(deferred)
        month = shift_month(first_close, item.sequence - 1, 1)
        _, close, due = await card_cycle(db, account_id, month, cache)
        return close, due
    return item.statement_on, item.due_on


async def apply(db, command, context, evidence):
    if isinstance(command, BookCreate):
        book = SolusBook(user_id=context.user_id, name=command.name, kind=command.kind,
                         currency=command.currency, timezone=command.timezone)
        db.add(book)
        await db.flush()
        return {"book_id": book.id, "revision": 0}
    book = await book_for(db, context.user_id, command.book_id)
    if isinstance(command,(InvestmentBuy,InvestmentSell,ValuationPut)):
        from app.services.solus.investments import apply_investment
        return await apply_investment(db,command,context,evidence)
    if isinstance(command, Reconcile):
        from hashlib import sha256
        from app.services.solus.reconciliation import validate
        if command.closes_on > datetime.now(ZoneInfo(book.timezone)).date():
            raise SolusError("Reconcile an actual issued statement, not a future estimate.")
        view = await validate(db, context, command)
        record = SolusReconciliation(book_id=book.id, account_id=command.account_id,
            opens_after=command.opens_after, closes_on=command.closes_on,
            source_hash=sha256(command.original.encode()).hexdigest(), original=command.original,
            matches=[m.model_dump() for m in command.matches], basis_hash=view['basis_hash'], revision=book.revision)
        db.add(record)
        await db.flush()
        return {'book_id':book.id,'reconciliation_id':record.id,'revision':book.revision,'status':'reconciled'}
    if isinstance(command, ImportCommit):
        from app.services.solus.imports import parse_csv
        source_hash, rows = await parse_csv(db, context.user_id, book.id, command.original)
        if source_hash != command.source_hash:
            raise SolusError("Import text changed since preview.", 409)
        if await db.scalar(select(SolusImport.id).where(SolusImport.book_id == book.id, SolusImport.source_hash == source_hash)):
            raise SolusError("This original was already imported.", 409)
        results = []
        for row in rows:
            result = await apply(db, row, context, evidence)
            results.append({"event_id": result["event_id"], "occurrence_key": row.occurrence_key})
        imported = SolusImport(book_id=book.id, source_hash=source_hash, original=command.original, rows=results)
        db.add(imported)
        await db.flush()
        return {"book_id": book.id, "import_id": imported.id, "rows": len(results), "revision": book.revision}
    if isinstance(command, AccountReportPut):
        account = await account_for(db, book.id, command.account_id)
        if account.report_role != command.expected_role:
            raise SolusError("Account reporting classification changed; reread it.", 409)
        account.report_role = command.report_role
        account.report_note = command.note
        book.revision += 1
        await db.flush()
        return {"book_id": book.id, "account_id": account.id, "report_role": account.report_role}
    if isinstance(command, AccountCreate):
        account = SolusAccount(book_id=book.id, name=command.name, account_type=command.account_type,
                               currency=command.currency, scale=currency_scale(command.currency))
        db.add(account)
        book.revision += 1
        await db.flush()
        return {"book_id": book.id, "account_id": account.id}
    if isinstance(command, CardCalendarPut):
        account = await account_for(db, book.id, command.account_id)
        if account.account_type != "liability":
            raise SolusError("A card calendar belongs to a liability account.")
        calendar = await db.get(SolusCardCalendar, account.id)
        if command.expected_version != (calendar.version if calendar else 0):
            raise SolusError("Card calendar changed; reread its version.", 409)
        effective = command.effective_period or (datetime.now(ZoneInfo(book.timezone)).strftime("%Y-%m") if calendar else "0001-01")
        if not calendar and effective != "0001-01":
            raise SolusError("The first calendar applies to historical data; change its effective month on a subsequent version.")
        if calendar and any(v["effective_period"] > effective for v in calendar.history):
            raise SolusError("Calendar changes must follow the latest effective month.", 409)
        if calendar and book.closed_through and date.fromisoformat(effective+'-01') <= book.closed_through:
            raise SolusError("Reopen this period before changing its card calendar.",409)
        history = list(calendar.history) if calendar else []
        unbilled = command.unbilled_account_id
        if "unbilled_account_id" not in command.model_fields_set and history:
            unbilled = history[-1].get("unbilled_account_id")
        if unbilled:
            linked = await account_for(db, book.id, unbilled)
            if linked.id == account.id or linked.account_type != "liability" or linked.currency != account.currency:
                raise SolusError("Unbilled principal needs a separate liability account in the same currency/book.")
        if calendar and not history:
            history.append({"effective_period": "0001-01", "closing_day": calendar.closing_day,
                "due_day": calendar.due_day, "due_offset": calendar.due_offset, "version": calendar.version})
        if calendar:
            calendar.closing_day, calendar.due_day, calendar.due_offset = command.closing_day, command.due_day, command.due_offset
            calendar.version += 1
        else:
            calendar = SolusCardCalendar(account_id=account.id, book_id=book.id,
                closing_day=command.closing_day, due_day=command.due_day, due_offset=command.due_offset)
            db.add(calendar)
        await db.flush()
        calendar.history = history + [{"effective_period": effective, "closing_day": command.closing_day,
            "due_day": command.due_day, "due_offset": command.due_offset, "version": calendar.version, "unbilled_account_id": unbilled}]
        await db.flush()
        book.revision += 1
        return {"book_id": book.id, "version": calendar.version, "revision": book.revision}
    if isinstance(command, CardStatementPut):
        account = await account_for(db, book.id, command.account_id)
        if book.closed_through and command.opens_after < book.closed_through:
            raise SolusError("Reopen this period before changing its card statement.", 409)
        if not await db.get(SolusCardCalendar, account.id):
            raise SolusError("Configure the card calendar before recording a statement.")
        statement = (await db.execute(select(SolusCardStatement).where(
            SolusCardStatement.account_id == account.id, SolusCardStatement.period == command.period,
        ))).scalar_one_or_none()
        if command.expected_version != (statement.version if statement else 0):
            raise SolusError("Statement changed; reread its version.", 409)
        neighbors = (await db.execute(select(SolusCardStatement).where(
            SolusCardStatement.account_id == account.id, SolusCardStatement.period != command.period,
            SolusCardStatement.opens_after < command.closes_on, SolusCardStatement.closes_on > command.opens_after,
        ))).scalars().all()
        if neighbors:
            raise SolusError("Statement cycles cannot overlap.", 409)
        if statement is None:
            statement = SolusCardStatement(account_id=account.id, book_id=book.id, period=command.period)
            db.add(statement)
        else:
            statement.version += 1
        statement.opens_after, statement.closes_on, statement.due_on = command.opens_after, command.closes_on, command.due_on
        statement.evidence = evidence
        if command.event_ids is not None:
            if len(set(command.event_ids)) != len(command.event_ids):
                raise SolusError("A statement event can be linked only once.")
            others = (await db.execute(select(SolusCardStatement).where(SolusCardStatement.account_id==account.id,SolusCardStatement.period!=command.period))).scalars().all()
            used = {event_id for other in others for event_id in (other.event_ids or [])}
            for event_id in command.event_ids:
                original = await db.get(SolusEvent,event_id)
                linked = await db.scalar(select(SolusPosting.id).where(SolusPosting.event_id==event_id,SolusPosting.account_id==account.id))
                if not original or original.book_id!=book.id or not original.posted or not linked or original.kind=="card_purchase" or original.occurred_on>command.closes_on:
                    raise SolusError("Statement link needs a posted non-scheduled movement on this card before closing.")
                if event_id in used:
                    raise SolusError("Movement already belongs to another statement; correct that statement first.",409)
            statement.event_ids = command.event_ids
        statement.opens_after, statement.closes_on, statement.due_on = command.opens_after, command.closes_on, command.due_on
        from app.services.solus.reconciliation import signed_minor
        statement.stated_balance_minor = signed_minor(command.stated_balance, account.currency) if command.stated_balance is not None else None
        statement.minimum_due_minor = minor(command.minimum_due, account.currency, allow_zero=True) if command.minimum_due is not None else None
        statement.evidence = evidence
        # Original schedules remain immutable. Reports overlay the statement's
        # supplied dates for that closing month; command receipts retain editions.
        book.revision += 1
        await db.flush()
        return {"book_id": book.id, "statement_id": statement.id, "version": statement.version}
    if isinstance(command, ClosePeriod):
        if command.expected_revision != book.revision:
            raise SolusError("Ledger changed; reread the revision.", 409)
        if command.through and command.through > datetime.now(ZoneInfo(book.timezone)).date():
            raise SolusError("Cannot close a future period.")
        book.closed_through = command.through
        book.revision += 1
        return {"book_id": book.id, "revision": book.revision}
    if isinstance(command, PlanPut):
        account = await account_for(db, book.id, command.account_id)
        if account.currency != command.currency:
            raise SolusError("Plan currency must match its account.")
        allowed = {"budget": {"expense"}, "recurring": {"expense", "income"}, "goal": {"asset"}}
        if account.account_type not in allowed[command.kind]:
            raise SolusError(f"{command.kind} needs an account of type: {', '.join(sorted(allowed[command.kind]))}.")
        plan = (await db.execute(select(SolusPlan).where(SolusPlan.book_id == book.id,
            SolusPlan.plan_key == command.plan_key))).scalar_one_or_none()
        if command.expected_version != (plan.version if plan else 0):
            raise SolusError("Plan changed; reread its version.", 409)
        if plan and plan.kind != command.kind:
            raise SolusError("A plan cannot change its kind.", 409)
        content = command.model_dump(mode="json", exclude={"operation", "command_key", "expected_version", "book_id"})
        if plan:
            plan.content, plan.active = content, command.active
            plan.version += 1
        else:
            plan = SolusPlan(book_id=book.id, plan_key=command.plan_key, kind=command.kind, content=content, active=command.active)
            db.add(plan)
        await db.flush()
        book.revision += 1
        return {"book_id": book.id, "plan_id": plan.id, "version": plan.version}
    if isinstance(command, Reverse):
        original = await db.get(SolusEvent, command.event_id)
        if not original or original.book_id != book.id or not original.posted:
            raise SolusError("Posted event not found.", 404)
        if original.reverses_id:
            raise SolusError("Reverse the original event through a correction, not a reversal of a reversal.", 409)
        from app.services.solus.investments import assert_reversible
        await assert_reversible(db,original)
        if command.occurred_on < original.occurred_on:
            raise SolusError("Reversal cannot precede the original event.")
        existing = await db.scalar(select(SolusEvent.id).where(SolusEvent.reverses_id == original.id))
        if existing:
            raise SolusError("Event already reversed.", 409)
        # A settled obligation must release payments before its principal can be reversed.
        items = (await db.execute(select(SolusScheduleItem.id).where(SolusScheduleItem.event_id == original.id))).scalars().all()
        for item_id in items:
            if await effective_settled(db, item_id):
                raise SolusError("Reverse linked payments before reversing this settled obligation.", 409)
        postings = (await db.execute(select(SolusPosting).where(SolusPosting.event_id == original.id))).scalars().all()
        details = {"original_kind": original.kind}
        if original.details.get("billed_on"):
            details["billed_on"] = max(original.details["billed_on"],command.occurred_on.isoformat())
        event = await create_event(db, book, context, f"reverse:{original.id}", "reversal", command.reason,
                                   command.occurred_on, evidence, details, original.id)
        for posting in postings:
            db.add(SolusPosting(book_id=book.id, event_id=event.id, account_id=posting.account_id, amount_minor=-posting.amount_minor))
    elif isinstance(command, Post):
        totals, accounts, amounts = defaultdict(int), {}, []
        for line in command.lines:
            account = await account_for(db, book.id, line.account_id)
            value = minor(line.amount, account.currency) * (1 if line.side == "debit" else -1)
            accounts[account.id] = account
            totals[account.currency] += value
            amounts.append((account, value))
        if any(totals.values()):
            raise SolusError("Journal must balance separately in every currency.")
        # Accounting effects are net per account. Opposite lines on the same
        # receivable must never manufacture settlement capacity without reducing
        # that receivable; retain the submitted form in the source/receipt.
        net = defaultdict(int)
        for account, value in amounts:
            net[account.id] += value
        amounts = [(accounts[account_id], value) for account_id, value in net.items() if value]
        if len(amounts) < 2:
            raise SolusError("Journal needs a nonzero movement between distinct accounts.")
        validate_semantics(command.kind, amounts)
        if command.booked_on and command.booked_on > datetime.now(ZoneInfo(book.timezone)).date():
            raise SolusError("A future booking date cannot represent an actual posted event.")
        if await db.scalar(select(SolusEvent.id).where(SolusEvent.book_id == book.id, SolusEvent.occurrence_key == command.occurrence_key)):
            raise SolusError("Occurrence already recorded; use its receipt or correct the original.", 409)
        details = {"booked_on": (command.booked_on or command.occurred_on).isoformat()}
        if command.kind == "card_purchase":
            details["first_statement_on"] = command.first_statement_on.isoformat() if command.first_statement_on else None
        if command.billed_on:
            if command.billed_on > datetime.now(ZoneInfo(book.timezone)).date():
                raise SolusError("A future billed date cannot represent a confirmed statement line.")
            if not any(a.account_type == "liability" for a, _ in amounts):
                raise SolusError("A billed date belongs to a liability statement movement.")
            details["billed_on"] = command.billed_on.isoformat()
        if command.related_event_id:
            related = await db.get(SolusEvent, command.related_event_id)
            if not related or related.book_id != book.id or not related.posted:
                raise SolusError("Related event not found in this book.", 404)
            details["related_event_id"] = related.id
        if command.document:
            details["document"] = command.document.model_dump(mode="json")
        event = await create_event(db, book, context, command.occurrence_key, command.kind, command.description,
                                   command.occurred_on, evidence, details)
        for account, value in amounts:
            db.add(SolusPosting(book_id=book.id, event_id=event.id, account_id=account.id, amount_minor=value))
        await db.flush()
        if command.kind == "card_purchase":
            cards = [(a, -v) for a, v in amounts if a.account_type == "liability" and v < 0]
            if len(cards) != 1:
                raise SolusError("Card purchase requires one credited card liability.")
            if any(a.account_type == "liability" and v > 0 for a, v in amounts):
                raise SolusError("Card purchase cannot include a second debt movement.")
            card, total = cards[0]
            calendar = await db.get(SolusCardCalendar, card.id)
            _, first_close, first_due = await booking_cycle(db, card.id, command.booked_on or command.occurred_on)
            if command.first_statement_on:
                if command.first_statement_on < first_close:
                    raise SolusError("First billed statement cannot precede the purchase's booking cycle.")
                _, first_close, first_due = await card_cycle(db, card.id, command.first_statement_on)
                if first_close != command.first_statement_on:
                    raise SolusError("First statement date must match the configured/overridden close.")
            if command.first_due_on and command.first_due_on != first_due:
                raise SolusError("First due date differs from this card's calendar; record a statement override first.")
            if command.schedule_amount and minor(command.schedule_amount, command.schedule_currency) != total:
                raise SolusError("Installment principal must equal the credited card amount.")
            if command.schedule_currency and command.schedule_currency != card.currency:
                raise SolusError("Installment currency must match the card.")
            for index, amount in enumerate(allocate(total, command.installments)):
                close = shift_month(first_close, index, calendar.closing_day)
                _, close, due = await card_cycle(db, card.id, close)
                db.add(SolusScheduleItem(book_id=book.id, event_id=event.id, sequence=index + 1,
                    statement_on=close, due_on=due, currency=card.currency, scale=card.scale, amount_minor=amount))
        elif command.first_due_on:
            total = minor(command.schedule_amount, command.schedule_currency)
            # Generic obligations need matching receivable/payable principal, not an arbitrary forecast.
            principal = sum(abs(v) for a, v in amounts if a.currency == command.schedule_currency and
                            ((command.kind == "invoice" and a.account_type == "asset" and v > 0) or
                             (command.kind in {"bill", "loan_disbursement"} and a.account_type == "liability" and v < 0)))
            if command.kind not in {"invoice", "bill", "loan_disbursement"} or total != principal:
                raise SolusError("Schedule must match this invoice's receivable or bill's payable.")
            for index, (due, amount) in enumerate(zip(monthly_dates(command.first_due_on, command.installments), allocate(total, command.installments))):
                db.add(SolusScheduleItem(book_id=book.id, event_id=event.id, sequence=index + 1,
                    due_on=due, currency=command.schedule_currency, scale=currency_scale(command.schedule_currency), amount_minor=amount))
        await db.flush()
        used_items = set()
        settlement_totals = defaultdict(int)
        for allocation in command.settlements:
            if allocation.item_id in used_items:
                raise SolusError("A schedule item can be allocated only once per payment.")
            used_items.add(allocation.item_id)
            item = await db.get(SolusScheduleItem, allocation.item_id)
            if not item or item.book_id != book.id:
                raise SolusError("Schedule item not found in this book.", 404)
            if await db.scalar(select(SolusEvent.id).where(SolusEvent.reverses_id == item.event_id)):
                raise SolusError("Cannot settle a reversed obligation.", 409)
            value = minor(allocation.amount, item.currency)
            if value + await effective_settled(db, item.id) > item.amount_minor:
                raise SolusError("Allocation exceeds the remaining obligation.")
            obligation_lines = (await db.execute(select(SolusPosting).where(SolusPosting.event_id == item.event_id))).scalars().all()
            matching = [(a, v) for a, v in amounts if a.currency == item.currency and any(
                p.account_id == a.id and p.amount_minor * v < 0 for p in obligation_lines) and a.account_type in {"asset", "liability"}]
            if not matching:
                raise SolusError("Payment must settle the obligation's receivable/payable account.")
            if len({a.id for a, _ in matching}) != 1:
                raise SolusError("Ambiguous settlement account; post the payment with one target principal account.")
            settlement_account = matching[0][0].id
            settlement_totals[settlement_account] += value
            available = sum(abs(v) for a, v in matching)
            if settlement_totals[settlement_account] > available:
                raise SolusError("Allocations exceed the payment's settlement amount.")
            db.add(SolusSettlement(book_id=book.id, payment_id=event.id, item_id=item.id, amount_minor=value))
    else:
        raise SolusError("Unsupported accounting command.")
    await db.flush()
    await db.execute(update(SolusEvent).where(SolusEvent.id == event.id).values(posted=True))
    book.revision += 1
    await db.flush()
    return {"book_id": book.id, "event_id": event.id, "revision": book.revision}


async def create_event(db, book, context, key, kind, description, occurred_on, evidence, details, reverses_id=None):
    if occurred_on > datetime.now(ZoneInfo(book.timezone)).date():
        raise SolusError("A future event is a plan, not a posted actual.")
    if book.closed_through and occurred_on <= book.closed_through:
        raise SolusError("Period is closed; explicitly reopen it before correction.", 409)
    event = SolusEvent(user_id=context.user_id, book_id=book.id, occurrence_key=key, kind=kind,
        description=description, occurred_on=occurred_on, evidence=evidence, details=details,
        actor=context.agent_id, request_id=context.request_id, reverses_id=reverses_id)
    db.add(event)
    await db.flush()
    return event


def validate_semantics(kind, amounts):
    debit = {a.account_type for a, v in amounts if v > 0}
    credit = {a.account_type for a, v in amounts if v < 0}
    required = {
        "expense": ({"expense"}, {"asset"}), "income": ({"asset"}, {"income"}),
        "card_purchase": ({"expense"}, {"liability"}), "debt_payment": ({"liability"}, {"asset"}),
        "loan_disbursement": ({"asset"}, {"liability"}), "invoice": ({"asset"}, {"income"}),
        "bill": ({"expense"}, {"liability"}),
        "transfer": ({"asset"}, {"asset"}), "refund": ({"asset", "liability"}, {"expense"}),
        "invoice_payment": ({"asset"}, {"asset"}), "bill_payment": ({"liability"}, {"asset"}),
        "reimbursement": ({"asset"}, {"asset"}),
        "credit_note": ({"income", "liability"}, {"asset", "expense"}),
    }
    if kind in required:
        wanted_debit, wanted_credit = required[kind]
        if (wanted_debit and not debit & wanted_debit) or (wanted_credit and not credit & wanted_credit):
            raise SolusError(f"{kind} has incompatible account sides/types; use the correct operation.")
    if kind == "opening" and ("equity" not in debit | credit or not (debit | credit) & {"asset", "liability"}):
        raise SolusError("Opening balance needs an asset/liability and opening equity.")
    if kind in {"transfer", "debt_payment", "loan_disbursement", "invoice_payment", "bill_payment", "reimbursement"} and "income" in debit | credit:
        raise SolusError("Transfers and debt/receivable settlements cannot create earned income.")
