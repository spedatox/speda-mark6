# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Exact, owner-scoped reads. Scheduled amounts never enter actual posting totals."""
from collections import defaultdict
from datetime import date
from sqlalchemy import select
from app.models.solus_ledger import (
    SolusBook, SolusAccount, SolusEvent, SolusPosting, SolusScheduleItem,
    SolusPlan, SolusCardCalendar, SolusCardStatement, SolusSettlement,
)
from app.services.solus.commands import book_for, effective_settled, effective_dates, card_cycle, SolusError
from app.services.solus.calendars import day_in_month
from app.services.solus.money import money


def period_bounds(period):
    import calendar
    try:
        if len(period) != 7:
            raise ValueError()
        first = date.fromisoformat(period + "-01")
    except (ValueError, TypeError):
        raise SolusError("Period must be a real YYYY-MM.") from None
    return first, date(first.year, first.month, calendar.monthrange(first.year, first.month)[1])


async def books(db, user_id):
    values = (await db.execute(select(SolusBook).where(SolusBook.user_id == user_id).order_by(SolusBook.created_at))).scalars().all()
    return [{"id": b.id, "name": b.name, "kind": b.kind, "currency": b.currency, "timezone": b.timezone,
             "revision": b.revision, "closed_through": b.closed_through.isoformat() if b.closed_through else None} for b in values]


async def accounts(db, user_id, book_id):
    await book_for(db, user_id, book_id)
    values = (await db.execute(select(SolusAccount).where(SolusAccount.book_id == book_id).order_by(SolusAccount.name))).scalars().all()
    calendars = {c.account_id: c for c in (await db.execute(select(SolusCardCalendar).where(SolusCardCalendar.book_id == book_id))).scalars()}
    return [{"id": a.id, "name": a.name, "account_type": a.account_type, "currency": a.currency,
             "scale": a.scale, "closed": a.closed, "report_role": a.report_role, "report_note": a.report_note,
             "card_calendar": ({"closing_day": calendars[a.id].closing_day, "due_day": calendars[a.id].due_day,
                "due_offset": calendars[a.id].due_offset, "version": calendars[a.id].version,
                "history": calendars[a.id].history} if a.id in calendars else None)} for a in values]


async def events(db, user_id, book_id, *, before=None, limit=50, event_id=None):
    await book_for(db, user_id, book_id)
    query = select(SolusEvent).where(SolusEvent.book_id == book_id, SolusEvent.posted.is_(True))
    if event_id:
        query = query.where(SolusEvent.id == event_id)
    if before:
        # Stable cursor uses persisted (created_at,id); dates are never user supplied cursors.
        anchor = await db.get(SolusEvent, before)
        if not anchor or anchor.book_id != book_id:
            raise SolusError("Event cursor not found.", 404)
        from sqlalchemy import or_, and_
        query = query.where(or_(SolusEvent.created_at < anchor.created_at,
                               and_(SolusEvent.created_at == anchor.created_at, SolusEvent.id < anchor.id)))
    rows = (await db.execute(query.order_by(SolusEvent.created_at.desc(), SolusEvent.id.desc()).limit(min(limit, 100) + 1))).scalars().all()
    selected = rows[:min(limit, 100)]
    ids = [e.id for e in selected]
    postings = (await db.execute(select(SolusPosting, SolusAccount).join(SolusAccount,
        SolusAccount.id == SolusPosting.account_id).where(SolusPosting.event_id.in_(ids)))).all() if ids else []
    grouped = defaultdict(list)
    for p, a in postings:
        grouped[p.event_id].append({"account_id": a.id, "account": a.name, "account_type": a.account_type,
            "side": "debit" if p.amount_minor > 0 else "credit", "amount": money(abs(p.amount_minor), a.scale), "currency": a.currency})
    return {"items": [{"id": e.id, "kind": e.kind, "description": e.description, "occurred_on": e.occurred_on.isoformat(),
        "booked_on": e.details.get("booked_on", e.occurred_on.isoformat()), "reverses_id": e.reverses_id,
        "lines": grouped[e.id], "document": e.details.get("document"), "related_event_id": e.details.get("related_event_id"), "evidence": e.evidence, "actor": e.actor} for e in selected],
        "next_cursor": selected[-1].id if len(rows) > len(selected) else None}


async def summary(db, user_id, book_id, period):
    first, last = period_bounds(period)
    book = await book_for(db, user_id, book_id)
    aggregate, balances, scales = defaultdict(lambda: defaultdict(int)), defaultdict(int), {}
    # Python integers keep aggregations exact even beyond SQLite SUM's integer range.
    query = select(SolusPosting.amount_minor, SolusAccount.id, SolusAccount.currency,
        SolusAccount.scale, SolusAccount.account_type, SolusEvent.occurred_on).join(SolusAccount, SolusAccount.id == SolusPosting.account_id).join(
        SolusEvent, SolusEvent.id == SolusPosting.event_id).where(SolusPosting.book_id == book_id,
            SolusEvent.posted.is_(True), SolusEvent.occurred_on <= last).execution_options(yield_per=500)
    async for amount, account_id, currency, scale, account_type, occurred_on in await db.stream(query):
        balances[account_id] += amount
        scales[currency] = scale
        if occurred_on >= first:
            aggregate[currency][account_type] += amount
    all_accounts = (await db.execute(select(SolusAccount).where(SolusAccount.book_id == book_id))).scalars().all()
    return {"book_id": book_id, "period": period, "revision": book.revision, "basis": "posted journal / occurrence date",
        "totals": {c: {"expense": money(v["expense"], scales[c]), "income": money(-v["income"], scales[c]),
                       "net_income": money(-v["income"] - v["expense"], scales[c])} for c, v in aggregate.items()},
        "balances": [{"account_id": a.id, "name": a.name, "type": a.account_type, "currency": a.currency,
                      "report_role": a.report_role, "report_note": a.report_note, "amount": money(balances[a.id] * (1 if a.account_type in {"asset", "expense"} else -1), a.scale)} for a in all_accounts],
        "scope": "Posted events only. Installment obligations and forecasts are separate. No currency conversion is implied."}


async def schedules(db, user_id, book_id, *, period=None, limit=100, after=None):
    await book_for(db, user_id, book_id)
    query = select(SolusScheduleItem, SolusEvent).join(SolusEvent, SolusEvent.id == SolusScheduleItem.event_id).where(
        SolusScheduleItem.book_id == book_id, SolusEvent.posted.is_(True))
    bounds = period_bounds(period) if period else None
    if after:
        query = query.where(SolusScheduleItem.id > after)
    # A supplied bank statement may move an installment into another due month.
    # Filter the effective dates before paginating, never the original schedule.
    query = query.order_by(SolusScheduleItem.id).execution_options(yield_per=100)
    rows = []
    cancelled_ids, paid_by_item, date_cache = await schedule_state(db, book_id)
    async for item, event in await db.stream(query):
        date_cache[("event", event.id)] = event
        dates = await effective_dates(db, item, date_cache)
        if bounds and not bounds[0] <= dates[1] <= bounds[1]:
            continue
        rows.append((item, event, dates))
        if len(rows) > min(limit, 100):
            break
    result = []
    for item, event, (statement_on, due_on) in rows[:min(limit, 100)]:
        cancelled = event.id in cancelled_ids
        paid = paid_by_item[item.id]
        result.append({"id": item.id, "event_id": item.event_id, "description": event.description,
            "sequence": item.sequence, "due_on": due_on.isoformat(), "statement_on": statement_on.isoformat() if statement_on else None,
            "amount": money(item.amount_minor, item.scale), "settled": money(paid, item.scale),
            "remaining": money(0 if cancelled else item.amount_minor - paid, item.scale), "currency": item.currency,
            "status": "cancelled" if cancelled else "settled" if paid == item.amount_minor else "partial" if paid else "scheduled"})
    return {"items": result, "next_cursor": result[-1]["id"] if len(rows) > len(result) else None}


async def schedule_state(db, book_id):
    """Batch state in this report snapshot; avoid a query per installment."""
    reversed_ids = select(SolusEvent.reverses_id).where(SolusEvent.book_id == book_id,
        SolusEvent.posted.is_(True), SolusEvent.reverses_id.is_not(None))
    cancelled = set((await db.execute(reversed_ids)).scalars())
    paid = defaultdict(int)
    query = select(SolusSettlement.item_id, SolusSettlement.amount_minor).join(SolusEvent,
        SolusEvent.id == SolusSettlement.payment_id).where(SolusSettlement.book_id == book_id,
            SolusEvent.posted.is_(True), SolusEvent.id.not_in(reversed_ids)).execution_options(yield_per=500)
    async for item_id, amount in await db.stream(query):
        paid[item_id] += amount
    cards = (await db.execute(select(SolusPosting.event_id, SolusPosting.account_id).join(SolusAccount,
        SolusAccount.id == SolusPosting.account_id).join(SolusEvent, SolusEvent.id == SolusPosting.event_id).where(
            SolusPosting.book_id == book_id, SolusPosting.amount_minor < 0,
            SolusAccount.account_type == "liability", SolusEvent.kind == "card_purchase"))).all()
    return cancelled, paid, {("card", event_id): account_id for event_id, account_id in cards}


async def plans(db, user_id, book_id):
    await book_for(db, user_id, book_id)
    values = (await db.execute(select(SolusPlan).where(SolusPlan.book_id == book_id).order_by(SolusPlan.id).limit(100))).scalars().all()
    return [{"id": p.id, "plan_key": p.plan_key, "kind": p.kind, "version": p.version, "active": p.active, **p.content} for p in values]


async def forecast(db, user_id, book_id, period):
    """Obligations and rules only; a forecast never posts an actual event."""
    first, last = period_bounds(period)
    book = await book_for(db, user_id, book_id)
    result, totals, estimates = [], defaultdict(int), defaultdict(int)
    cancelled_ids, paid_by_item, date_cache = await schedule_state(db, book_id)
    query = select(SolusScheduleItem, SolusEvent).join(SolusEvent, SolusEvent.id == SolusScheduleItem.event_id).where(
        SolusScheduleItem.book_id == book_id, SolusEvent.posted.is_(True))
    async for item, event in await db.stream(query.execution_options(yield_per=500)):
        date_cache[("event", event.id)] = event
        _, due = await effective_dates(db, item, date_cache)
        if not first <= due <= last:
            continue
        if event.id in cancelled_ids:
            continue
        remaining = item.amount_minor - paid_by_item[item.id]
        if not remaining:
            continue
        direction = 1 if event.kind == "invoice" else -1
        totals[item.currency] += remaining * direction
        result.append({"source": "obligation", "name": event.description, "date": due.isoformat(),
            "direction": "in" if direction > 0 else "out", "amount": money(remaining, item.scale), "currency": item.currency})
    from app.services.solus.money import minor, currency_scale
    for plan in (await db.execute(select(SolusPlan).where(SolusPlan.book_id == book_id, SolusPlan.active.is_(True)))).scalars():
        content = plan.content
        if content["starts_on"] > last.isoformat() or (content.get("ends_on") and content["ends_on"] < first.isoformat()):
            continue
        account = await db.get(SolusAccount, content["account_id"])
        amount = minor(content["amount"], content["currency"])
        if plan.kind == "budget":
            # Native-currency budget comparison is an expense-account actual, not cashflow.
            actual = sum((await db.execute(select(SolusPosting.amount_minor).join(SolusEvent,
                SolusEvent.id == SolusPosting.event_id).where(SolusPosting.account_id == account.id,
                    SolusEvent.posted.is_(True), SolusEvent.occurred_on.between(first, last)))).scalars())
            result.append({"source": "budget", "name": content["name"], "amount": money(amount, account.scale),
                "actual": money(actual, account.scale), "remaining": money(amount - actual, account.scale), "currency": account.currency})
        elif plan.kind == "recurring":
            from app.services.solus.calendars import day_in_month
            from datetime import timedelta
            starts = date.fromisoformat(content["starts_on"])
            frequency = content.get("frequency", "monthly")
            if frequency == "annual":
                dates = [day_in_month(first.year, starts.month, starts.day)] if first.month == starts.month else []
            elif frequency == "weekly":
                anchor = starts + timedelta(days=max(0, (first - starts).days + 6)//7*7)
                dates = [anchor + timedelta(days=i*7) for i in range(5) if anchor + timedelta(days=i*7) <= last]
            else:
                dates = [day_in_month(first.year, first.month, starts.day)]
            for due in dates:
                if due < starts or (content.get("ends_on") and due > date.fromisoformat(content["ends_on"])):
                    continue
                direction = 1 if account.account_type == "income" else -1
                estimates[account.currency] += amount * direction
                result.append({"source": "recurring estimate", "name": content["name"], "date": due.isoformat(),
                    "direction": "in" if direction > 0 else "out", "amount": money(amount, account.scale), "currency": account.currency})
        else:
            result.append({"source": "goal", "name": content["name"], "amount": content["amount"], "currency": account.currency})
    return {"period": period, "revision": book.revision, "items": result,
        "net_scheduled": {c: money(v, currency_scale(c)) for c, v in totals.items()},
        "net_recurring_estimates": {c: money(v, currency_scale(c)) for c, v in estimates.items()},
        "scope": "Forecast only. Confirmed obligations and recurring estimates have separate totals to avoid double counting. No automatic booking."}


async def statement(db, user_id, book_id, account_id, period):
    first, _ = period_bounds(period)
    book = await book_for(db, user_id, book_id)
    account = await db.get(SolusAccount, account_id)
    if not account or account.book_id != book.id:
        raise SolusError("Card account not found.", 404)
    calendar = await db.get(SolusCardCalendar, account_id)
    if not calendar:
        raise SolusError("Configure the card's statement dates in Solus first.")
    previous, close, due = await card_cycle(db, account_id, first)
    declared = (await db.execute(select(SolusCardStatement).where(SolusCardStatement.account_id==account_id))).scalars().all()
    statement_dates = {event_id: item.closes_on for item in declared for event_id in (item.event_ids or [])}
    date_cache, schedules_by_event = {}, defaultdict(list)
    items = (await db.execute(select(SolusScheduleItem).join(SolusPosting,
        SolusPosting.event_id == SolusScheduleItem.event_id).where(
            SolusPosting.account_id == account_id, SolusScheduleItem.statement_on.is_not(None)))).scalars()
    for item in items:
        schedules_by_event[item.event_id].append(item)
    query = select(SolusPosting, SolusEvent).join(SolusEvent, SolusEvent.id == SolusPosting.event_id).where(
        SolusPosting.account_id == account_id, SolusEvent.posted.is_(True)).execution_options(yield_per=500)
    deltas, principal_balance = [], 0
    async for p, e in await db.stream(query):
        booking = date.fromisoformat(e.details.get("booked_on", e.occurred_on.isoformat()))
        if booking > close:
            continue
        principal_balance -= p.amount_minor
        billing = statement_dates.get(e.id, date.fromisoformat(e.details.get("billed_on", booking.isoformat())))
        if e.reverses_id in statement_dates:
            billing = max(booking,statement_dates[e.reverses_id])
        if e.kind == "card_purchase":
            date_cache[("event", e.id)] = e
            for item in schedules_by_event[e.id]:
                billed_on, _ = await effective_dates(db, item, date_cache, account_id)
                if billed_on <= close:
                    deltas.append((billed_on, item.amount_minor, e.id, e.description, "installment"))
        elif e.reverses_id:
            if e.reverses_id in schedules_by_event:
                # Release only already billed principal; future installments are cancelled.
                for item in schedules_by_event[e.reverses_id]:
                    billed_on, _ = await effective_dates(db, item, date_cache, account_id)
                    if billed_on <= close:
                        deltas.append((max(booking, billed_on), -item.amount_minor, e.id, e.description, "reversal"))
            else:
                deltas.append((billing, -p.amount_minor, e.id, e.description, "reversal"))
        else:
            deltas.append((billing, -p.amount_minor, e.id, e.description, e.kind))
    editions = [v for v in calendar.history if v["effective_period"] <= period]
    linked_id = max(editions, key=lambda v: (v["effective_period"],v["version"])).get("unbilled_account_id") if editions else None
    unbilled_balance = 0
    if linked_id:
        linked = await db.get(SolusAccount, linked_id)
        if not linked or linked.book_id != book.id or linked.currency != account.currency or linked.account_type != "liability":
            raise SolusError("Unbilled account mapping is invalid.",409)
        values = (await db.execute(select(SolusPosting.amount_minor).join(SolusEvent,SolusEvent.id==SolusPosting.event_id).where(
            SolusPosting.account_id==linked_id, SolusEvent.posted.is_(True), SolusEvent.occurred_on<=close))).scalars()
        unbilled_balance = -sum(values)
        principal_balance += unbilled_balance
    opening = sum(v for d, v, *_ in deltas if d <= previous)
    current = [(d, v, eid, desc, kind) for d, v, eid, desc, kind in deltas if previous < d <= close]
    closing = opening + sum(v for _, v, *_ in current)
    observed = (await db.execute(select(SolusCardStatement).where(SolusCardStatement.account_id == account_id,
        SolusCardStatement.period == period))).scalar_one_or_none()
    stated = observed.stated_balance_minor if observed else None
    return {"book_id": book_id, "account_id": account_id, "card": account.name, "currency": account.currency,
        "period": period, "opens_after": previous.isoformat(), "closes_on": close.isoformat(), "due_on": due.isoformat(),
        "revision": book.revision, "status": "compared" if stated is not None else "estimated",
        "statement_version": observed.version if observed else 0,
        "unbilled_principal": money(principal_balance - closing, account.scale),
        "opening_billed_balance": money(opening, account.scale), "closing_billed_balance": money(closing, account.scale),
        "total_principal_balance": money(principal_balance, account.scale),
        "stated_balance": money(stated, account.scale) if stated is not None else None,
        "difference": money(closing - stated, account.scale) if stated is not None else None,
        "minimum_due": money(observed.minimum_due_minor, account.scale) if observed and observed.minimum_due_minor is not None else None,
        "lines": [{"date": d.isoformat(), "amount": money(v, account.scale), "event_id": eid, "description": desc, "kind": kind}
                  for d, v, eid, desc, kind in sorted(current)],
        "scope": "Known posted bookings and billed installment principal. Closing day is inclusive; bank statement overrides win. Minimum due is supplied from the bank, never guessed. Comparison is not reconciliation."}


async def business_report(db, user_id, book_id, period):
    """Accrual accounts and open dated obligations; no statutory tax inference."""
    _, last = period_bounds(period)
    result = await summary(db, user_id, book_id, period)
    balance_sheet = defaultdict(lambda: defaultdict(int))
    from app.services.solus.money import minor, currency_scale
    for account in result['balances']:
        # Balances may be negative; minor() intentionally parses input magnitudes.
        value = account['amount']
        signed = (-1 if value.startswith('-') else 1) * minor(value.lstrip('-'), account['currency'], allow_zero=True)
        balance_sheet[account['currency']][account['type']] += signed
    sheets = {}
    for currency, values in balance_sheet.items():
        profit = values['income'] - values['expense']
        sheets[currency] = {k: money(values[k], currency_scale(currency)) for k in ['asset','liability','equity']}
        sheets[currency]['retained_result'] = money(profit, currency_scale(currency))
        sheets[currency]['check'] = money(values['asset']-values['liability']-values['equity']-profit, currency_scale(currency))
    aging = []
    query = select(SolusScheduleItem, SolusEvent).join(SolusEvent, SolusEvent.id == SolusScheduleItem.event_id).where(
        SolusScheduleItem.book_id == book_id, SolusEvent.posted.is_(True), SolusEvent.occurred_on <= last,
        SolusEvent.kind.in_(['invoice','bill']))
    for item, event in (await db.execute(query)).all():
        if await db.scalar(select(SolusEvent.id).where(SolusEvent.reverses_id == event.id, SolusEvent.occurred_on <= last)):
            continue
        # Aging is as of this report, excluding payments made after month end.
        from app.models.solus_ledger import SolusSettlement
        reversed_ids = select(SolusEvent.reverses_id).where(SolusEvent.reverses_id.is_not(None), SolusEvent.occurred_on <= last)
        settled = sum((await db.execute(select(SolusSettlement.amount_minor).join(SolusEvent,
            SolusEvent.id == SolusSettlement.payment_id).where(SolusSettlement.item_id == item.id,
                SolusEvent.posted.is_(True), SolusEvent.occurred_on <= last, SolusEvent.id.not_in(reversed_ids)))).scalars())
        remaining = item.amount_minor - settled
        if remaining:
            overdue = max(0, (last-item.due_on).days)
            aging.append({'event_id':event.id,'description':event.description,'document':event.details.get('document'),
                'kind':event.kind,'due_on':item.due_on.isoformat(),'remaining':money(remaining,item.scale),
                'currency':item.currency,'overdue_days':overdue,'bucket':'current' if not overdue else '1-30' if overdue<=30 else '31-60' if overdue<=60 else '61-90' if overdue<=90 else '90+'})
    # Review/clearing accounts are bookkeeping offsets, not established holdings or debt.
    from app.models.solus_ledger import SolusReconciliation
    from datetime import datetime
    from zoneinfo import ZoneInfo
    book = await book_for(db, user_id, book_id)
    as_of = min(last, datetime.now(ZoneInfo(book.timezone)).date())
    sources = {}
    records = (await db.execute(select(SolusReconciliation).where(SolusReconciliation.book_id == book_id,
        SolusReconciliation.closes_on <= as_of).order_by(SolusReconciliation.closes_on))).scalars()
    for record in records:
        sources[record.account_id] = record
    financial, review = [], []
    for account in result['balances']:
        if account['report_role'] == 'review':
            review.append(account)
        elif account['type'] in {'asset', 'liability'}:
            source = sources.get(account['account_id'])
            verified = None
            if source:
                from app.services.solus.reconciliation import basis
                current = await basis(db, user_id, book_id, source.account_id, source.opens_after, source.closes_on)
                if current['basis_hash'] == source.basis_hash:
                    verified = current['closing_balance']
            financial.append({**account, 'verified_through': source.closes_on.isoformat() if verified is not None else None,
                'source_balance': verified,
                'coverage_complete': bool(verified is not None and source.closes_on == as_of)})
    return {**result,'balance_sheet':sheets,'aging':aging,
        'as_of': as_of.isoformat(), 'financial_accounts': financial, 'review_accounts': review,
        'provisional': bool(review) or any(not a['coverage_complete'] for a in financial),
        'scope':'Accrual income and expenses; balance sheet and aging as of month end. Native currencies stay separate. Supplied tax journals only; no filing calculation.'}


async def reconciliations(db, user_id, book_id):
    await book_for(db,user_id,book_id)
    from app.models.solus_ledger import SolusReconciliation
    from app.services.solus.reconciliation import basis
    rows=(await db.execute(select(SolusReconciliation).where(SolusReconciliation.book_id==book_id).order_by(
        SolusReconciliation.created_at.desc()).limit(50))).scalars()
    result=[]
    for row in rows:
        current=await basis(db,user_id,book_id,row.account_id,row.opens_after,row.closes_on)
        result.append({'id':row.id,'account_id':row.account_id,'opens_after':row.opens_after.isoformat(),
            'closes_on':row.closes_on.isoformat(),'source_hash':row.source_hash,'revision':row.revision,
            'status':'reconciled' if current['basis_hash']==row.basis_hash else 'needs review; accounting basis changed'})
    return result
