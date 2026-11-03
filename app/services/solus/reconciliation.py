# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reconciliation requires exact balances and every source/movement match."""
import csv
import io
import json
from hashlib import sha256
from datetime import date
from collections import defaultdict
from sqlalchemy import select
from app.models.solus_ledger import SolusEvent, SolusPosting, SolusCardCalendar
from app.services.solus.commands import book_for, account_for, SolusError
from app.services.solus.money import minor, money, currency_scale


def signed_minor(value, currency):
    if not isinstance(value, str):
        raise SolusError("Supply an exact signed decimal string.")
    return (-1 if value.startswith('-') else 1) * minor(value[1:] if value.startswith('-') else value, currency, allow_zero=True)


def parse_statement(original, currency):
    if not isinstance(original, str) or len(original.encode()) > 1_000_000:
        raise SolusError("Statement CSV must be text under 1 MB.")
    reader = csv.DictReader(io.StringIO(original.lstrip('\ufeff')), strict=True)
    if not reader.fieldnames or set(reader.fieldnames) != {'date','description','amount','event_ref'} or len(reader.fieldnames) != 4:
        raise SolusError("Statement CSV headers: date,description,amount,event_ref.")
    result, used = [], set()
    try:
        for row in reader:
            if len(result) >= 500 or None in row or any(v is None for v in row.values()):
                raise SolusError("Use at most 500 complete statement rows.")
            ref = row['event_ref'].strip()
            if not ref or len(ref) > 200 or ref in used:
                raise SolusError("Each statement row needs a unique, stable event_ref.")
            used.add(ref)
            occurred = date.fromisoformat(row['date'].strip())
            amount = signed_minor(row['amount'].strip(), currency)
            if not amount:
                raise SolusError("Zero-value statement rows carry no movement; exclude them from reconciliation.")
            result.append({'row_ref':ref,'date':occurred.isoformat(),'description':row['description'],
                'amount':money(amount, currency_scale(currency)), 'minor':amount})
    except csv.Error as exc:
        raise SolusError(f"Invalid statement CSV: {exc}") from None
    return result


async def basis(db, user_id, book_id, account_id, opens_after, closes_on):
    book = await book_for(db, user_id, book_id)
    account = await account_for(db, book_id, account_id)
    if account.account_type not in {'asset','liability'}:
        raise SolusError("Reconcile an asset or liability account.")
    if closes_on <= opens_after:
        raise SolusError("Close must follow the previous close.")
    movements = defaultdict(lambda:{'minor':0,'description':'','dates':set()})
    opening, closing = 0, 0
    is_card = bool(await db.get(SolusCardCalendar, account_id))
    if is_card:
        from app.services.solus.reports import statement
        view = await statement(db,user_id,book_id,account_id,closes_on.strftime('%Y-%m'))
        if view['opens_after'] != opens_after.isoformat() or view['closes_on'] != closes_on.isoformat():
            raise SolusError("For cards, use the configured or bank-overridden statement boundaries.")
        opening, closing = signed_minor(view['opening_billed_balance'],account.currency),signed_minor(view['closing_billed_balance'],account.currency)
        for line in view['lines']:
            movement=movements[line['event_id']]
            movement['minor']+=signed_minor(line['amount'],account.currency)
            movement['description']=line['description'];movement['dates'].add(line['date'])
    else:
        sign = 1 if account.account_type=='asset' else -1
        query=select(SolusPosting,SolusEvent).join(SolusEvent,SolusEvent.id==SolusPosting.event_id).where(
            SolusPosting.account_id==account_id,SolusEvent.posted.is_(True))
        async for posting,event in await db.stream(query.execution_options(yield_per=500)):
            booking=date.fromisoformat(event.details.get('booked_on',event.occurred_on.isoformat()))
            if booking>closes_on:
                continue
            value=posting.amount_minor*sign
            closing+=value
            if booking<=opens_after:
                opening+=value
            else:
                movement=movements[event.id]
                movement['minor']+=value;movement['description']=event.description;movement['dates'].add(booking.isoformat())
    entries=[{'event_id':eid,'amount':money(v['minor'],account.scale),'minor':v['minor'],'description':v['description'],
        'dates':sorted(v['dates'])} for eid,v in sorted(movements.items()) if v['minor']]
    signature={'account_id':account_id,'opens_after':opens_after.isoformat(),'closes_on':closes_on.isoformat(),
        'opening':opening,'closing':closing,'entries':entries}
    return {'revision':book.revision,'currency':account.currency,'opening_balance':money(opening,account.scale),
        'closing_balance':money(closing,account.scale),'entries':entries,'basis':'billed_statement' if is_card else 'booked_account',
        'basis_hash':sha256(json.dumps(signature,sort_keys=True).encode()).hexdigest()}


async def preview(db, user_id, book_id, payload):
    opens_after=date.fromisoformat(payload['opens_after']);closes_on=date.fromisoformat(payload['closes_on'])
    view=await basis(db,user_id,book_id,payload['account_id'],opens_after,closes_on)
    rows=parse_statement(payload['original'],view['currency'])
    return {**view,'source_hash':sha256(payload['original'].encode()).hexdigest(),
        'rows':[{**r,'candidates':[e for e in view['entries'] if e['minor']==r['minor']]} for r in rows],
        'scope':'Candidates are suggestions only. Explicitly review every match. Positive amounts increase this account balance; negative amounts decrease it.'}


async def validate(db, context, command):
    if sha256(command.original.encode()).hexdigest()!=command.source_hash:
        raise SolusError("Statement source changed after preview; review it again.",409)
    view=await basis(db,context.user_id,command.book_id,command.account_id,command.opens_after,command.closes_on)
    if command.expected_revision!=view['revision']:
        raise SolusError("Ledger changed since reconciliation preview; review again.",409)
    rows=parse_statement(command.original,view['currency'])
    opening=signed_minor(command.opening_balance,view['currency']);closing=signed_minor(command.closing_balance,view['currency'])
    if opening!=signed_minor(view['opening_balance'],view['currency']) or closing!=signed_minor(view['closing_balance'],view['currency']):
        raise SolusError("Statement balances differ from the ledger. Record and explain the missing movements first.",409)
    if opening+sum(r['minor'] for r in rows)!=closing:
        raise SolusError("Statement rows do not explain its opening-to-closing balance.",409)
    matches={m.row_ref:m.event_id for m in command.matches}
    if len(matches)!=len(command.matches) or set(matches)!={r['row_ref'] for r in rows}:
        raise SolusError("Every source row must have one explicitly reviewed match.",409)
    expected={e['event_id']:e['minor'] for e in view['entries']}
    if len(set(matches.values()))!=len(matches) or set(matches.values())!=set(expected):
        raise SolusError("Every ledger movement must match exactly once; unmatched or duplicate movements remain.",409)
    for row in rows:
        if expected[matches[row['row_ref']]]!=row['minor']:
            raise SolusError("Matched amounts differ; do not infer a partial allocation.",409)
        if view['basis']=='booked_account':
            movement=next(e for e in view['entries'] if e['event_id']==matches[row['row_ref']])
            if row['date'] not in movement['dates']:
                raise SolusError("Bank booking dates differ; correct the evidenced booking before reconciliation.",409)
    return view
