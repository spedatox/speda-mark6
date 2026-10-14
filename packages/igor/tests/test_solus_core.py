# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Accounting invariants, statement cutoffs and transactional failures."""
from datetime import date
from uuid import uuid4
import random

import pytest
from sqlalchemy import event, select, text, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.exc import IntegrityError
import app.models
from app.database import Base
from app.core.context import AgentContext
from app.models.user import User
from app.models.solus_ledger import SolusEvent, SolusCommand, SolusScheduleItem
from app.services.memory_store import run_financial_command
from app.services.solus import reports
from app.services.solus.guards import install_guards
from app.services.solus.money import minor, money, allocate, monthly_dates


def test_exact_money_and_allocation_properties():
    rng = random.Random(1776)
    for _ in range(1000):
        total, count = rng.randrange(10**12), rng.randrange(1, 601)
        parts = allocate(total, count)
        assert sum(parts) == total and min(parts) >= 0 and max(parts) - min(parts) <= 1
        assert minor(money(total, 2), "TRY", allow_zero=True) == total
    assert allocate(5, 8) == [1, 1, 1, 1, 1, 0, 0, 0]
    assert monthly_dates(date(2028, 1, 31), 3) == [date(2028, 1, 31), date(2028, 2, 29), date(2028, 3, 31)]


@pytest.mark.asyncio
async def test_review_accounts_are_not_presented_as_established_debt(ledger):
    context, factory, book, ids = ledger
    await run_financial_command(context, {"operation": "post", "command_key": str(uuid4()),
        "book_id": book, "occurrence_key": str(uuid4()), "kind": "journal", "description": "Unclassified receipt", "occurred_on": "2026-08-10",
        "lines": [{"account_id": ids['Bank'], "side": "debit", "amount": "100000.00"},
                  {"account_id": ids['Card'], "side": "credit", "amount": "100000.00"}]})
    await run_financial_command(context, {"operation": "account_report_put", "command_key": str(uuid4()),
        "book_id": book, "account_id": ids['Card'], "expected_role": "financial", "report_role": "review",
        "note": "Unknown receipt classification, not established debt"})
    async with factory() as db:
        result = await reports.business_report(db, 1, book, '2026-08')
        assert result['provisional'] is True
        assert ids['Card'] not in {a['account_id'] for a in result['financial_accounts']}
        assert next(a for a in result['review_accounts'] if a['account_id'] == ids['Card'])['amount'] == '100000.00'
        assert next(a for a in result['balances'] if a['account_id'] == ids['Bank'])['amount'] == '100000.00'
        assert result['balance_sheet']['TRY']['check'] == '0.00'


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1.00", "1e3", "4,914.00", "4.914", "0.00", 100, 1.01])
def test_bad_money_is_rejected(value):
    with pytest.raises(ValueError):
        minor(value, "TRY")


@pytest.fixture
async def ledger(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'ledger.db').as_posix()}")
    @event.listens_for(engine.sync_engine, "connect")
    def pragmas(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=15000")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.run_sync(install_guards)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add_all([User(id=1, name="Owner", timezone="Europe/Istanbul"), User(id=2, name="Other", timezone="UTC")])
        await db.commit()
    async with factory() as db:
        context = AgentContext(agent_id="owner", user_id=1, session_id=0, request_id=str(uuid4()), triggered_by="user",
            trigger_payload={}, output_mode="respond", model="", system_prompt="", conversation_history=[], db=db, timezone="Europe/Istanbul")
        book = await run_financial_command(context, {"operation": "book_create", "command_key": str(uuid4()),
            "name": "Personal", "kind": "personal"})
        ids = {}
        for name, kind in [("Bank", "asset"), ("Card", "liability"), ("Clothing", "expense"), ("Earnings", "income"), ("Equity", "equity")]:
            result = await run_financial_command(context, {"operation": "account_create", "command_key": str(uuid4()),
                "book_id": book["book_id"], "name": name, "account_type": kind})
            ids[name] = result["account_id"]
        yield context, factory, book["book_id"], ids
    await engine.dispose()


def purchase(book_id, ids, **changes):
    return {"operation": "post", "command_key": str(uuid4()), "book_id": book_id,
        "occurrence_key": str(uuid4()), "kind": "expense", "description": "Shoes", "occurred_on": "2026-08-10",
        "lines": [{"account_id": ids["Clothing"], "side": "debit", "amount": "6000.00"},
                  {"account_id": ids["Bank"], "side": "credit", "amount": "6000.00"}], **changes}


async def test_receipt_retry_currency_and_isolated_session(ledger):
    ctx, factory, book, ids = ledger
    user = await ctx.db.get(User, 1)
    user.name = "Uncommitted chat mutation"
    command = purchase(book, ids)
    first = await run_financial_command(ctx, command)
    assert await run_financial_command(ctx, command) == first
    async with factory() as reader:
        assert (await reader.get(User, 1)).name == "Owner"
        assert await reader.scalar(select(func.count()).select_from(SolusEvent)) == 1
        result = await reports.summary(reader, 1, book, "2026-08")
        assert result["totals"]["TRY"]["expense"] == "6000.00"
    changed = {**command, "description": "Different input"}
    with pytest.raises(ValueError, match="Idempotency"):
        await run_financial_command(ctx, changed)
    await ctx.db.rollback()


async def test_posted_history_protected_even_through_sql(ledger):
    ctx, factory, book, ids = ledger
    receipt = await run_financial_command(ctx, purchase(book, ids))
    async with factory() as db:
        with pytest.raises(IntegrityError):
            await db.execute(text("DELETE FROM solus_events WHERE id=:id"), {"id": receipt["event_id"]})
        await db.rollback()
        with pytest.raises(IntegrityError):
            await db.execute(text("UPDATE solus_postings SET amount_minor=1 WHERE event_id=:id"), {"id": receipt["event_id"]})
        await db.rollback()


async def test_native_currency_separation_and_explicit_fx(ledger):
    ctx, factory, book, ids = ledger
    usd = {}
    for name,kind in [('USD bank','asset'),('USD spending','expense'),('USD clearing','equity')]:
        usd[kind]=(await run_financial_command(ctx,{'operation':'account_create','command_key':str(uuid4()),
            'book_id':book,'name':name,'account_type':kind,'currency':'USD'}))['account_id']
    await run_financial_command(ctx,purchase(book,ids))
    await run_financial_command(ctx,purchase(book,ids,lines=[
        {'account_id':usd['expense'],'side':'debit','amount':'100.00'},
        {'account_id':usd['asset'],'side':'credit','amount':'100.00'}]))
    with pytest.raises(ValueError,match='every currency'):
        await run_financial_command(ctx,purchase(book,ids,kind='fx',lines=[
            {'account_id':ids['Bank'],'side':'debit','amount':'100.00'},
            {'account_id':usd['asset'],'side':'credit','amount':'100.00'}]))
    await run_financial_command(ctx,purchase(book,ids,kind='fx',lines=[
        {'account_id':ids['Bank'],'side':'debit','amount':'4000.00'},
        {'account_id':ids['Equity'],'side':'credit','amount':'4000.00'},
        {'account_id':usd['equity'],'side':'debit','amount':'100.00'},
        {'account_id':usd['asset'],'side':'credit','amount':'100.00'}]))
    async with factory() as db:
        view=await reports.summary(db,1,book,'2026-08')
        assert view['totals']['TRY']['expense']=='6000.00'
        assert view['totals']['USD']['expense']=='100.00'
        assert all(v['income']=='0.00' for v in view['totals'].values())


async def test_uncertain_command_resolution_fences_late_arrivals(ledger):
    from app.services.memory_store import resolve_financial_command
    ctx, factory, book, ids = ledger
    cancelled = purchase(book,ids)
    result = await resolve_financial_command(ctx,cancelled['command_key'])
    assert result['status']=='cancelled'
    with pytest.raises(ValueError,match='Idempotency'):
        await run_financial_command(ctx,cancelled)
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(SolusEvent))==0
    committed = purchase(book,ids)
    receipt = await run_financial_command(ctx,committed)
    assert await resolve_financial_command(ctx,committed['command_key'])==receipt
    assert await run_financial_command(ctx,committed)==receipt


async def test_projection_failure_rolls_back_journal_receipt_and_sources(ledger, monkeypatch):
    ctx, factory, book, ids = ledger
    async def fail(*args):
        raise RuntimeError("Injected index failure")
    monkeypatch.setattr("app.services.solus.projections.refresh", fail)
    command = purchase(book, ids)
    with pytest.raises(RuntimeError, match="Injected"):
        await run_financial_command(ctx, command)
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(SolusEvent)) == 0
        assert not await db.scalar(select(SolusCommand.id).where(SolusCommand.command_key == command["command_key"]))


async def test_concurrent_retry_and_cross_book_scope(ledger):
    import asyncio
    from dataclasses import replace
    ctx, factory, book, ids = ledger
    command = purchase(book, ids)
    async def write():
        async with factory() as db:
            return await run_financial_command(replace(ctx, db=db), command)
    receipts = await asyncio.gather(write(), write(), write())
    assert receipts[0] == receipts[1] == receipts[2]
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(SolusEvent)) == 1
        with pytest.raises(ValueError, match='Book not found'):
            await reports.summary(db, 2, book, '2026-08')
    other = await run_financial_command(ctx, {'operation':'book_create','command_key':str(uuid4()),'name':'Business','kind':'business'})
    with pytest.raises(ValueError, match='Account not found'):
        await run_financial_command(ctx, purchase(other['book_id'], ids))


async def test_statement_override_and_calendar_editions(ledger):
    ctx, factory, book, ids = ledger
    await run_financial_command(ctx, {'operation':'card_calendar_put','command_key':str(uuid4()),'book_id':book,
        'account_id':ids['Card'],'closing_day':31,'due_offset':10,'expected_version':0})
    receipt = await run_financial_command(ctx, purchase(book,ids,kind='card_purchase',occurred_on='2026-08-20',installments=2,
        lines=[{'account_id':ids['Clothing'],'side':'debit','amount':'100.00'},{'account_id':ids['Card'],'side':'credit','amount':'100.00'}]))
    await run_financial_command(ctx, {'operation':'card_statement_put','command_key':str(uuid4()),'book_id':book,
        'account_id':ids['Card'],'period':'2026-08','opens_after':'2026-07-31','closes_on':'2026-08-30','due_on':'2026-09-12',
        'stated_balance':'50.00','minimum_due':'10.00','expected_version':0})
    await run_financial_command(ctx, {'operation':'card_calendar_put','command_key':str(uuid4()),'book_id':book,
        'account_id':ids['Card'],'closing_day':15,'due_day':25,'expected_version':1,'effective_period':'2026-10'})
    async with factory() as db:
        august = await reports.statement(db,1,book,ids['Card'],'2026-08')
        september = await reports.statement(db,1,book,ids['Card'],'2026-09')
        october = await reports.statement(db,1,book,ids['Card'],'2026-10')
        assert august['closes_on']=='2026-08-30' and august['due_on']=='2026-09-12'
        assert august['unbilled_principal']=='50.00'
        assert august['closing_billed_balance']=='50.00' and august['difference']=='0.00'
        assert september['closes_on']=='2026-09-30' and october['closes_on']=='2026-10-15'
        items=(await reports.schedules(db,1,book))['items']
        assert any(i['statement_on']=='2026-08-30' and i['due_on']=='2026-09-12' for i in items)
        # An issued statement can move a due date across a reporting month.
    await run_financial_command(ctx, {'operation':'card_statement_put','command_key':str(uuid4()),'book_id':book,
        'account_id':ids['Card'],'period':'2026-08','opens_after':'2026-07-31','closes_on':'2026-08-30','due_on':'2026-10-01',
        'expected_version':1})
    async with factory() as db:
        assert not (await reports.schedules(db,1,book,period='2026-09'))['items']
        assert len((await reports.schedules(db,1,book,period='2026-10'))['items']) == 2


async def test_changed_close_reassigns_booking_cycle_and_preserves_deferral(ledger):
    ctx,factory,book,ids=ledger
    await run_financial_command(ctx,{'operation':'card_calendar_put','command_key':str(uuid4()),'book_id':book,
        'account_id':ids['Card'],'closing_day':31,'due_offset':10,'expected_version':0})
    await run_financial_command(ctx,purchase(book,ids,kind='card_purchase',occurred_on='2026-08-31',installments=2,
        lines=[{'account_id':ids['Clothing'],'side':'debit','amount':'100.00'},
               {'account_id':ids['Card'],'side':'credit','amount':'100.00'}]))
    await run_financial_command(ctx,{'operation':'card_statement_put','command_key':str(uuid4()),'book_id':book,
        'account_id':ids['Card'],'period':'2026-08','opens_after':'2026-07-31','closes_on':'2026-08-30','due_on':'2026-09-09','expected_version':0})
    async with factory() as db:
        august=await reports.statement(db,1,book,ids['Card'],'2026-08')
        september=await reports.statement(db,1,book,ids['Card'],'2026-09')
        assert august['closing_billed_balance']=='0.00'
        assert september['opening_billed_balance']=='0.00'
        assert september['closing_billed_balance']=='50.00'
        items=(await reports.schedules(db,1,book))['items']
        assert {i['statement_on'] for i in items}=={'2026-09-30','2026-10-31'}
    await run_financial_command(ctx,purchase(book,ids,kind='card_purchase',occurred_on='2026-08-20',installments=1,
        first_statement_on='2026-10-31',lines=[{'account_id':ids['Clothing'],'side':'debit','amount':'10.00'},
               {'account_id':ids['Card'],'side':'credit','amount':'10.00'}]))
    async with factory() as db:
        september=await reports.statement(db,1,book,ids['Card'],'2026-09')
        assert september['closing_billed_balance']=='50.00'
        october=await reports.statement(db,1,book,ids['Card'],'2026-10')
        assert october['closing_billed_balance']=='110.00'


async def test_import_is_atomic_source_preserving_and_duplicate_safe(ledger):
    from hashlib import sha256
    from app.models.solus_ledger import SolusImport
    ctx, factory, book, ids = ledger
    original='date,description,kind,amount,debit_account,credit_account,currency,event_ref\n2026-08-10,Shoes,expense,10.00,Clothing,Bank,TRY,bank-a-001\n'
    command={'operation':'import_commit','command_key':str(uuid4()),'book_id':book,'original':original,'source_hash':sha256(original.encode()).hexdigest()}
    receipt=await run_financial_command(ctx,command)
    assert await run_financial_command(ctx,command)==receipt
    with pytest.raises(ValueError,match='already imported'):
        await run_financial_command(ctx,{**command,'command_key':str(uuid4())})
    # Another export of the same bank occurrence must not create a second expense.
    overlap=original.replace('Shoes','Changed description')
    with pytest.raises(ValueError,match='Occurrence already'):
        await run_financial_command(ctx,{**command,'command_key':str(uuid4()),'original':overlap,'source_hash':sha256(overlap.encode()).hexdigest()})
    invalid=original.replace('bank-a-001','bank-a-002')+'2026-08-10,Bad,expense,2.00,Unknown,Bank,TRY,bank-a-003\n'
    with pytest.raises(ValueError):
        await run_financial_command(ctx,{**command,'command_key':str(uuid4()),'original':invalid,'source_hash':sha256(invalid.encode()).hexdigest()})
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(SolusEvent))==1
        assert await db.scalar(select(SolusImport.original))==original


async def test_reconciliation_requires_every_match_and_detects_basis_change(ledger):
    from hashlib import sha256
    from app.models.solus_ledger import SolusBook
    ctx, factory, book, ids = ledger
    paid = await run_financial_command(ctx, purchase(book,ids))
    original='date,description,amount,event_ref\n2026-08-10,Shoes,-6000.00,row-1\n'
    async with factory() as db:
        revision=(await db.get(SolusBook,book)).revision
    command={'operation':'reconcile','command_key':str(uuid4()),'book_id':book,'account_id':ids['Bank'],
        'opens_after':'2026-07-31','closes_on':'2026-08-31','opening_balance':'0.00','closing_balance':'-6000.00',
        'original':original,'source_hash':sha256(original.encode()).hexdigest(),'expected_revision':revision,'matches':[]}
    with pytest.raises(ValueError,match='Every source row'):
        await run_financial_command(ctx,command)
    await run_financial_command(ctx,{**command,'matches':[{'row_ref':'row-1','event_id':paid['event_id']}]})
    async with factory() as db:
        assert (await reports.reconciliations(db,1,book))[0]['status']=='reconciled'
    await run_financial_command(ctx,purchase(book,ids,occurred_on='2026-08-20'))
    async with factory() as db:
        assert 'needs review' in (await reports.reconciliations(db,1,book))[0]['status']


async def test_invoice_partial_payment_credit_note_and_period_close(ledger):
    from app.models.solus_ledger import SolusBook
    ctx, factory, book, ids = ledger
    receivable = await run_financial_command(ctx,{'operation':'account_create','command_key':str(uuid4()),'book_id':book,'name':'Customer receivable','account_type':'asset'})
    principal=receivable['account_id']
    invoice = purchase(book,ids,kind='invoice',description='Work invoice',occurred_on='2026-08-10',
        first_due_on='2026-08-31',schedule_amount='1000.00',schedule_currency='TRY',document={'number':'INV-001','party':'Customer'},
        lines=[{'account_id':principal,'side':'debit','amount':'1000.00'},{'account_id':ids['Earnings'],'side':'credit','amount':'1000.00'}])
    receipt=await run_financial_command(ctx,invoice)
    async with factory() as db:
        item_id=await db.scalar(select(SolusScheduleItem.id).where(SolusScheduleItem.event_id==receipt['event_id']))
    with pytest.raises(ValueError, match='nonzero movement'):
        await run_financial_command(ctx,purchase(book,ids,kind='invoice_payment',lines=[
            {'account_id':principal,'side':'debit','amount':'400.00'},
            {'account_id':principal,'side':'credit','amount':'400.00'}],settlements=[{'item_id':item_id,'amount':'400.00'}]))
    payment=purchase(book,ids,kind='invoice_payment',occurred_on='2026-08-15',lines=[
        {'account_id':ids['Bank'],'side':'debit','amount':'400.00'},{'account_id':principal,'side':'credit','amount':'400.00'}],settlements=[{'item_id':item_id,'amount':'400.00'}])
    await run_financial_command(ctx,payment)
    await run_financial_command(ctx,purchase(book,ids,kind='credit_note',occurred_on='2026-08-20',related_event_id=receipt['event_id'],lines=[
        {'account_id':ids['Earnings'],'side':'debit','amount':'100.00'},{'account_id':principal,'side':'credit','amount':'100.00'}],settlements=[{'item_id':item_id,'amount':'100.00'}]))
    async with factory() as db:
        result=await reports.business_report(db,1,book,'2026-08')
        assert result['totals']['TRY']['income']=='900.00'
        assert result['aging'][0]['remaining']=='500.00'
        assert result['balance_sheet']['TRY']['check']=='0.00'
        revision=(await db.get(SolusBook,book)).revision
    await run_financial_command(ctx,{'operation':'close_period','command_key':str(uuid4()),'book_id':book,'through':'2026-08-31','expected_revision':revision})
    with pytest.raises(ValueError,match='Period is closed'):
        await run_financial_command(ctx,purchase(book,ids))


async def test_specific_cost_lots_conserve_basis_and_keep_valuation_out_of_income(ledger):
    from app.services.solus.investments import positions
    ctx,factory,book,ids=ledger
    holding=(await run_financial_command(ctx,{'operation':'account_create','command_key':str(uuid4()),'book_id':book,'name':'Holdings','account_type':'asset'}))['account_id']
    buy={'operation':'investment_buy','command_key':str(uuid4()),'occurrence_key':str(uuid4()),'book_id':book,
        'holding_account_id':holding,'cash_account_id':ids['Bank'],'instrument':'TEST','quantity':'3','unit_scale':0,'cost':'0.10','occurred_on':'2026-08-10'}
    acquired=await run_financial_command(ctx,buy)
    await run_financial_command(ctx,{'operation':'valuation_put','command_key':str(uuid4()),'book_id':book,
        'account_id':holding,'instrument':'TEST','as_of':'2026-08-10','value':'0.15'})
    async with factory() as db:
        view=await positions(db,1,book,'2026-08')
        assert view['valuations'][0]['unrealized_at_valuation']=='0.05'
        assert (await reports.summary(db,1,book,'2026-08'))['totals']['TRY']['income']=='0.00'
    receipts=[]
    for i in range(3):
        sold=await run_financial_command(ctx,{'operation':'investment_sell','command_key':str(uuid4()),'occurrence_key':str(uuid4()),
            'book_id':book,'lot_id':acquired['lot_id'],'cash_account_id':ids['Bank'],'gain_account_id':ids['Earnings'],
            'loss_account_id':ids['Clothing'],'quantity':'1','proceeds':'0.07','occurred_on':f'2026-08-{11+i}',
            'fee':'0.01' if i==0 else '0.00','fee_account_id':ids['Clothing']})
        receipts.append(sold)
    assert [r['cost_basis'] for r in receipts]==['0.03','0.03','0.04']
    with pytest.raises(ValueError,match='later lot disposals'):
        await run_financial_command(ctx,{'operation':'reverse','command_key':str(uuid4()),'book_id':book,
            'event_id':receipts[0]['event_id'],'occurred_on':'2026-08-20','reason':'Correction'})
    async with factory() as db:
        assert not (await positions(db,1,book,'2026-08'))['lots']
        result=await reports.summary(db,1,book,'2026-08')
        assert result['totals']['TRY']=={'income':'0.11','expense':'0.01','net_income':'0.10'}


async def test_card_statement_calendar_installments_and_payment(ledger):
    ctx, factory, book, ids = ledger
    # Owner configures dates; card names/date rules are data, never implementation constants.
    await run_financial_command(ctx, {"operation": "card_calendar_put", "command_key": str(uuid4()), "book_id": book,
        "account_id": ids["Card"], "closing_day": 10, "due_day": 20, "expected_version": 0})
    command = purchase(book, ids, kind="card_purchase", installments=3, occurred_on="2026-08-09", booked_on="2026-08-11",
        lines=[{"account_id": ids["Clothing"], "side": "debit", "amount": "6000.00"},
               {"account_id": ids["Card"], "side": "credit", "amount": "6000.00"}])
    receipt = await run_financial_command(ctx, command)
    async with factory() as db:
        august = await reports.statement(db, 1, book, ids["Card"], "2026-08")
        september = await reports.statement(db, 1, book, ids["Card"], "2026-09")
        assert august["closing_billed_balance"] == "0.00"
        assert september["closing_billed_balance"] == "2000.00"
        assert september["due_on"] == "2026-09-20"
        items = (await db.execute(select(SolusScheduleItem).where(SolusScheduleItem.event_id == receipt["event_id"]).order_by(SolusScheduleItem.sequence))).scalars().all()
        item_id = items[0].id
    payment = purchase(book, ids, kind="debt_payment", occurred_on="2026-09-18", description="Card payment",
        lines=[{"account_id": ids["Card"], "side": "debit", "amount": "2000.00"},
               {"account_id": ids["Bank"], "side": "credit", "amount": "2000.00"}], settlements=[{"item_id": item_id, "amount": "2000.00"}])
    await run_financial_command(ctx, payment)
    async with factory() as db:
        october = await reports.statement(db, 1, book, ids["Card"], "2026-10")
        assert october["opening_billed_balance"] == "2000.00"
        assert october["closing_billed_balance"] == "2000.00"  # prior payment offsets this cycle's new 2,000
        assert october["total_principal_balance"] == "4000.00"
        result = await reports.summary(db, 1, book, "2026-09")
        assert result["totals"]["TRY"]["expense"] == "0.00"


async def test_missing_calendar_and_bad_journal_save_nothing(ledger):
    ctx, factory, book, ids = ledger
    bad = purchase(book, ids, kind="card_purchase", lines=[
        {"account_id": ids["Clothing"], "side": "debit", "amount": "6000.00"},
        {"account_id": ids["Card"], "side": "credit", "amount": "6000.00"}])
    with pytest.raises(ValueError, match="Configure"):
        await run_financial_command(ctx, bad)
    invalid = purchase(book, ids)
    invalid["lines"][0]["amount"] = "6000.01"
    with pytest.raises(ValueError, match="balance"):
        await run_financial_command(ctx, invalid)
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(SolusEvent)) == 0

async def test_statement_billing_date_and_unbilled_principal(ledger):
    ctx, factory, book, ids = ledger
    unbilled=(await run_financial_command(ctx,{"operation":"account_create","command_key":str(uuid4()),"book_id":book,"name":"Unbilled principal","account_type":"liability"}))["account_id"]
    await run_financial_command(ctx,{"operation":"card_calendar_put","command_key":str(uuid4()),"book_id":book,"account_id":ids["Card"],"closing_day":1,"due_offset":10,"expected_version":0,"unbilled_account_id":unbilled})
    receipt=await run_financial_command(ctx,purchase(book,ids,kind="journal",occurred_on="2026-08-01",billed_on="2026-09-01",lines=[
        {"account_id":ids["Clothing"],"side":"debit","amount":"1000.00"},
        {"account_id":ids["Card"],"side":"credit","amount":"333.40"},
        {"account_id":unbilled,"side":"credit","amount":"666.60"}]))
    async with factory() as db:
        before=await reports.statement(db,1,book,ids["Card"],"2026-08")
        billed=await reports.statement(db,1,book,ids["Card"],"2026-09")
        assert before["closing_billed_balance"]=="0.00"
        assert billed["closing_billed_balance"]=="333.40"
        assert billed["total_principal_balance"]=="1000.00" and billed["unbilled_principal"]=="666.60"
        assert (await reports.summary(db,1,book,"2026-08"))["totals"]["TRY"]["expense"]=="1000.00"
    await run_financial_command(ctx,{"operation":"reverse","command_key":str(uuid4()),"book_id":book,"event_id":receipt["event_id"],"occurred_on":"2026-08-01","reason":"Replace source mapping"})
    async with factory() as db:
        view=await reports.statement(db,1,book,ids["Card"],"2026-09")
        assert view["closing_billed_balance"]=="0.00" and view["total_principal_balance"]=="0.00"

async def test_statement_membership_includes_printed_boundary_and_preserves_retry(ledger):
    import hashlib,json
    ctx,factory,book,ids=ledger
    await run_financial_command(ctx,{"operation":"card_calendar_put","command_key":str(uuid4()),"book_id":book,"account_id":ids["Card"],"closing_day":1,"due_offset":10,"expected_version":0})
    payload=purchase(book,ids,kind="bill",occurred_on="2026-08-01",lines=[{"account_id":ids["Clothing"],"side":"debit","amount":"100.00"},{"account_id":ids["Card"],"side":"credit","amount":"100.00"}])
    receipt=await run_financial_command(ctx,payload)
    await run_financial_command(ctx,{"operation":"card_statement_put","command_key":str(uuid4()),"book_id":book,"account_id":ids["Card"],"period":"2026-09","opens_after":"2026-08-01","closes_on":"2026-09-01","due_on":"2026-09-11","stated_balance":"100.00","minimum_due":"20.00","expected_version":0,"event_ids":[receipt["event_id"]]})
    async with factory() as db:
        assert (await reports.statement(db,1,book,ids["Card"],"2026-08"))["closing_billed_balance"]=="0.00"
        assert (await reports.statement(db,1,book,ids["Card"],"2026-09"))["difference"]=="0.00"
        canonical={**payload,"command_key":"older-schema-retry-key","evidence":[],"booked_on":None,"document":None,"related_event_id":None,"first_due_on":None,"first_statement_on":None,"installments":1,"schedule_amount":None,"schedule_currency":None,"settlements":[]}
        saved={**receipt,"command_key":canonical["command_key"]}
        db.add(SolusCommand(user_id=1,command_key=canonical["command_key"],payload_hash=hashlib.sha256(json.dumps(canonical,sort_keys=True,separators=(",", ":")).encode()).hexdigest(),payload=canonical,receipt=saved,actor="owner",request_id=str(uuid4())))
        await db.commit()
    assert await run_financial_command(ctx,canonical)==saved
    with pytest.raises(ValueError,match="Idempotency"):
        await run_financial_command(ctx,{**canonical,"description":"changed"})
