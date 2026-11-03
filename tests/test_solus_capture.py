import asyncio
import time
from dataclasses import replace
from datetime import datetime
from uuid import uuid4
import pytest
from sqlalchemy import select, update, func
from app.models.coordination import CoordinationRun, AgentStep, Room
from app.models.memory_serving import MemoryChange
from app.models.solus_ledger import SolusEvent, SolusScheduleItem, SolusCommand
from app.schemas.coordination import CreateRoom, RoomInput
from app.services.solus.store import run_financial_command
from app.services.solus.capture import capture, configure_routing
from app.services.solus import reports
from app.services.solus.commands import SolusError
from tests.test_worker_control import environment


@pytest.fixture
async def accounting(memory):
    bits,_ = await environment(memory)
    async with memory.sessions() as db:
        owner = replace(memory.context(),agent_id='owner',db=db)
        book = (await run_financial_command(owner,{'operation':'book_create','command_key':uuid4().hex,
            'name':'Personal','kind':'personal','currency':'TRY','timezone':'Europe/Istanbul'}))['book_id']
        ids = {}
        for name,kind in [('Bank','asset'),('Savings','asset'),('Card','liability'),('Clothing','expense'),('Salary','income')]:
            ids[name]=(await run_financial_command(owner,{'operation':'account_create','command_key':uuid4().hex,
                'book_id':book,'name':name,'account_type':kind}))['account_id']
        await run_financial_command(owner,{'operation':'card_calendar_put','command_key':uuid4().hex,
            'book_id':book,'account_id':ids['Card'],'closing_day':15,'due_offset':10,'expected_version':0})
        async def turn(message, stamp=datetime(2026,8,31,21,5)):
            room = await bits.coordinator.create(1,CreateRoom(lead='sentinel'))
            admitted = await bits.coordinator.start(1,room['id'],RoomInput(input_id=uuid4().hex,message=message,agent_id='sentinel'))
            async with memory.sessions() as writer,writer.begin():
                run = await writer.get(CoordinationRun,admitted['run_id'])
                run.status,run.executor,run.lease_until='running',bits.coordinator.identity,time.time()+300
                step = await writer.scalar(select(AgentStep).where(AgentStep.run_id==run.id))
                step.status='running'
                stored_room = await writer.get(Room,room['id'])
                source = await writer.scalar(select(MemoryChange).where(MemoryChange.session_id==stored_room.session_id))
                source.created_at=stamp
                ctx=replace(memory.context(),db=db,agent_id='sentinel',session_id=stored_room.session_id,
                    room_id=room['id'],run_id=run.id,step_id=step.id,request_id=step.id,coordination=bits.coordinator,
                    prompt_datetime='2026-10-25T22:00:00Z')
            return ctx
        config_ctx = await turn('Use Bank as default payment account, Card as default card account and Clothing as default expense category.')
        await configure_routing(config_ctx,{'operation':'routing_put','book_id':book,'expected_version':0,
            'payment_account':'Bank','card_account':'Card','expense_category':'Clothing'},
            'Use Bank as default payment account, Card as default card account and Clothing as default expense category.')
        yield bits,owner,book,ids,turn
    await bits.coordinator.shutdown()


async def test_relative_date_uses_source_timezone_and_retry_not_model_description(accounting):
    bits,_,book,_,turn=accounting
    ctx=await turn('Yesterday I bought shoes for 1.234,56 TRY.')
    facts={'kind':'expense','amount':'1234.56','quote':'Yesterday I bought shoes for 1.234,56 TRY.',
        'description':'Shoes','date_phrase':'Yesterday'}
    first=await capture(ctx,facts)
    assert first['status']=='success' and first['occurred_on']=='2026-08-31'
    again=await capture(ctx,{**facts,'description':'The model rephrased this purchase'})
    assert first==again
    async with bits.coordinator.sessions() as db:
        view=await reports.summary(db,1,book,'2026-08')
        assert view['totals']['TRY']['expense']=='1234.56'
        assert await db.scalar(select(func.count()).select_from(SolusEvent))==1
    assert not bits.client.calls


async def test_card_installments_conserve_total_and_payment_is_not_new_spending(accounting):
    bits,_,book,_,turn=accounting
    ctx=await turn('I bought shoes for 100 TRY in 3 installments.')
    receipt=await capture(ctx,{'kind':'card_purchase','amount':'100','quote':'I bought shoes for 100 TRY in 3 installments.',
        'description':'Shoes','installments':3})
    assert receipt['status']=='success'
    pay=await turn('I paid 33.34 TRY toward my credit card debt from Bank.')
    result=await capture(pay,{'kind':'card_payment','amount':'33.34','description':'Card repayment',
        'quote':'I paid 33.34 TRY toward my credit card debt from Bank.'})
    assert result['status']=='success' and result['accounting_kind']=='debt_payment'
    async with bits.coordinator.sessions() as db:
        amounts=list(await db.scalars(select(SolusScheduleItem.amount_minor).where(SolusScheduleItem.event_id==receipt['event_id'])))
        assert amounts==[3334,3333,3333]
        view=await reports.summary(db,1,book,'2026-09')
        assert view['totals']['TRY']['expense']=='100.00'


@pytest.mark.parametrize('quote,changes,field',[
    ('I will pay 100 TRY tomorrow.',{},'occurrence'),
    ('I did not pay 100 TRY.',{},'occurrence'),
    ('I bought shoes for 100 TRY last week.',{},'date_phrase'),
    ('I bought shoes for 100 TRY.',{'amount':'200'},'amount'),
    ('I bought shoes for 100 USD.',{},'currency'),
    ('I paid my credit card bill of 100 TRY.',{},'kind'),
    ('I bought shoes for 100 TRY.',{'from_account':'Savings'},'from_account'),
])
async def test_ambiguous_unsupported_or_contradictory_facts_do_not_post(accounting,quote,changes,field):
    bits,_,_,_,turn=accounting
    ctx=await turn(quote)
    result=await capture(ctx,{'kind':'expense','amount':'100','description':'Transaction','quote':quote,**changes})
    assert result['status']=='needs_information' and result['field']==field
    async with bits.coordinator.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(SolusEvent))==0


async def test_later_queued_source_and_cancelled_run_cannot_write(accounting):
    bits,_,_,_,turn=accounting
    ctx=await turn('I spent 100 TRY.')
    facts={'kind':'expense','amount':'100','description':'Transaction','quote':'I spent 100 TRY.'}
    async with bits.coordinator.sessions() as db,db.begin():
        await db.execute(update(CoordinationRun).where(CoordinationRun.id==ctx.run_id).values(cancelled=True))
    with pytest.raises(SolusError,match='fenced'): await capture(ctx,facts)
    async with bits.coordinator.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(SolusEvent))==0


async def test_atomic_retry_uses_one_economic_occurrence_and_raw_journal_is_refused(accounting):
    bits,_,book,ids,turn=accounting
    ctx=await turn('I spent 100 TRY.')
    facts={'kind':'expense','amount':'100','description':'Transaction','quote':'I spent 100 TRY.'}
    receipts=await asyncio.gather(capture(ctx,facts),capture(ctx,facts))
    assert receipts[0]==receipts[1]
    with pytest.raises(SolusError,match='free-form'):
        await run_financial_command(ctx,{'operation':'post','command_key':uuid4().hex,'book_id':book,
            'occurrence_key':uuid4().hex,'kind':'expense','description':'Unadmitted journal','occurred_on':'2026-09-01',
            'lines':[{'account_id':ids['Clothing'],'side':'debit','amount':'999'},
                     {'account_id':ids['Bank'],'side':'credit','amount':'999'}]})
    assert not bits.client.calls


async def test_count_and_per_installment_total_are_computed_from_source(accounting):
    bits,_,_,_,turn=accounting
    ctx=await turn('Yesterday I bought shoes in 3 installments of 100 TRY each.')
    result=await capture(ctx,{'kind':'card_purchase','amount':'100','description':'Shoes',
        'quote':'Yesterday I bought shoes in 3 installments of 100 TRY each.'})
    assert result['status']=='success' and result['amount']=='300.00' and result['occurred_on']=='2026-08-31'
    async with bits.coordinator.sessions() as db:
        amounts=list(await db.scalars(select(SolusScheduleItem.amount_minor).where(SolusScheduleItem.event_id==result['event_id'])))
        assert amounts==[10000,10000,10000]


async def test_owner_allocation_policy_settles_only_billed_installments_and_retry_is_stable(accounting):
    bits,_,book,_,turn=accounting
    quote='Use oldest billed as the default card payment allocation.'
    policy_context=await turn(quote)
    await configure_routing(policy_context,{'operation':'routing_put','book_id':book,'expected_version':1,
        'card_payment_allocation':'oldest_billed'},quote)
    purchase_context=await turn('I bought shoes for 100 TRY in 3 installments.')
    purchase=await capture(purchase_context,{'kind':'card_purchase','amount':'100','description':'Shoes',
        'quote':'I bought shoes for 100 TRY in 3 installments.'})
    payment_context=await turn('I paid 40 TRY toward my credit card debt from Bank.',datetime(2026,9,26,10))
    facts={'kind':'card_payment','amount':'40','description':'Repayment','quote':'I paid 40 TRY toward my credit card debt from Bank.'}
    receipt=await capture(payment_context,facts)
    assert receipt['settlement']=={'policy':'oldest_billed','allocated':'33.34','unallocated':'6.66','currency':'TRY'}
    assert (await capture(payment_context,facts))==receipt
    async with bits.coordinator.sessions() as db:
        from app.services.solus.commands import effective_settled
        items=list(await db.scalars(select(SolusScheduleItem).where(SolusScheduleItem.event_id==purchase['event_id']).order_by(SolusScheduleItem.sequence)))
        assert [await effective_settled(db,item.id) for item in items]==[3334,0,0]


async def test_owner_defaults_cannot_be_changed_by_a_purchase_or_another_agent(accounting):
    _,_,book,_,turn=accounting
    ctx=await turn('I bought shoes for 100 TRY.')
    with pytest.raises(SolusError,match='explicit owner instruction'):
        await configure_routing(ctx,{'operation':'routing_put','book_id':book,'expected_version':1,
            'payment_account':'Savings'},'I bought shoes for 100 TRY.')
    with pytest.raises(SolusError,match='Only Sentinel'):
        await capture(replace(ctx,agent_id='speda'),{'kind':'expense','amount':'100','description':'Shoes','quote':'I bought shoes for 100 TRY.'})


async def test_native_sentinel_loop_records_without_a_helper_model_or_manual_journal(accounting):
    bits,_,book,_,_=accounting
    from app.services.coordination import Coordinator
    from app.services.client_compat import ClientCompatibility
    from app.schemas.client_compat import ClientChat
    from app.skills.solus import SolusAccountingSkill
    from app.schemas.llm import LLMMessage, Block
    from tests.test_coordination import reply
    await bits.engine._registry.register_skill(SolusAccountingSkill())
    bits.coordinator.launch=Coordinator.launch.__get__(bits.coordinator)
    bits.client.script=[LLMMessage([Block(type='tool_use',id='record-owner-occurrence',name='solus_accounting',
        input={'operation':'capture','facts':{'kind':'expense','amount':'100','description':'Spending','quote':'I spent 100 TRY.'}})],'tool_use'),
        reply('Recorded the expense using your configured account.')]
    result=await ClientCompatibility(bits.coordinator).start(1,'sentinel',ClientChat(message='I spent 100 TRY.'))
    await bits.coordinator.live[result['run_id']]
    assert len(bits.client.calls)==2
    assert 'solus_accounting' in {tool['name'] for tool in bits.client.calls[0]['tools']}
    async with bits.coordinator.sessions() as db:
        events=list(await db.scalars(select(SolusEvent).where(SolusEvent.book_id==book)))
        assert len(events)==1 and events[0].actor=='sentinel'
        assert events[0].evidence[0]['source_authority']=='owner'
        assert (await bits.coordinator.inspect_run(1,result['run_id']))['status']=='succeeded'


async def test_original_uploaded_receipt_date_and_hash_survive_reupload(accounting):
    import hashlib
    from app.models.coordination import RoomAttachment,RoomEvent
    bits,_,_,_,turn=accounting
    content='2026-08-30 Bank purchase shoes for 100 TRY.'
    async def with_receipt():
        ctx=await turn('Record this receipt.')
        identity=uuid4().hex
        async with bits.coordinator.sessions() as db,db.begin():
            db.add(RoomAttachment(id=identity,room_id=ctx.room_id,name='receipt.txt',mime='text/plain',
                original=content.encode(),extracted=content,sha256=hashlib.sha256(content.encode()).hexdigest()))
            event=await db.scalar(select(RoomEvent).where(RoomEvent.run_id==ctx.run_id,RoomEvent.kind=='user_message'))
            event.payload={**event.payload,'attachments':[{'id':identity}]}
        return ctx,identity
    ctx,identity=await with_receipt()
    first=await capture(ctx,{'attachment_id':identity,'kind':'expense','amount':'100','description':'Shoes','quote':content})
    assert first['occurred_on']=='2026-08-30'
    again,other_id=await with_receipt()
    second=await capture(again,{'attachment_id':other_id,'kind':'expense','amount':'100','description':'Shoes','quote':content})
    assert second==first
    async with bits.coordinator.sessions() as db:
        events=list(await db.scalars(select(SolusEvent)))
        assert len(events)==1 and events[0].evidence[0]['source_authority']=='owner_uploaded_document'


async def test_bill_due_date_is_not_occurrence_date_and_document_payment_settles_it(accounting):
    bits,owner,book,_,turn=accounting
    payable=(await run_financial_command(owner,{'operation':'account_create','command_key':uuid4().hex,
        'book_id':book,'name':'Payables','account_type':'liability'}))['account_id']
    quote='I received unpaid bill BILL-27 for 100 TRY in Payables, due 2026-09-25.'
    ctx=await turn(quote)
    bill=await capture(ctx,{'kind':'bill','amount':'100','quote':quote,'description':'Bill',
        'to_account':'Payables','due_date_phrase':'2026-09-25','document_number':'BILL-27'})
    assert bill['status']=='success' and bill['occurred_on']=='2026-09-01'
    payment_quote='I paid bill BILL-27 in Payables from Bank for 100 TRY.'
    ctx=await turn(payment_quote,datetime(2026,9,25,10))
    payment=await capture(ctx,{'kind':'bill_payment','amount':'100','quote':payment_quote,'description':'Bill payment',
        'to_account':'Payables','from_account':'Bank','document_number':'BILL-27'})
    assert payment['settlement_status']=='matched' and payment['settlement']['allocated']=='100.00'
    async with bits.coordinator.sessions() as db:
        forecast=await reports.forecast(db,1,book,'2026-09')
        assert forecast['net_scheduled'].get('TRY','0.00')=='0.00'
        assert (await reports.summary(db,1,book,'2026-09'))['totals']['TRY']['expense']=='100.00'


@pytest.mark.parametrize('wording,stamp,expected',[
    ('last Monday',datetime(2026,9,1,10),'2026-08-31'),
    ('geçen pazartesi',datetime(2026,8,31,10),'2026-08-24'),
    ('3 days ago',datetime(2026,9,1,10),'2026-08-29'),
    ('2 gün önce',datetime(2026,9,1,10),'2026-08-30'),
])
async def test_relative_weekdays_and_elapsed_days_use_original_evidence(accounting,wording,stamp,expected):
    _,_,_,_,turn=accounting
    quote='I spent 100 TRY '+wording+'.'
    ctx=await turn(quote,stamp)
    result=await capture(ctx,{'kind':'expense','amount':'100','quote':quote,'description':'Expense'})
    assert result['status']=='success' and result['occurred_on']==expected


async def test_explicit_original_owner_source_across_chats_cannot_read_later_inputs(accounting):
    bits,_,_,_,turn=accounting
    quote='Yesterday I spent 100 TRY on shoes.'
    old=await turn(quote,datetime(2026,8,31,21,5))
    async with bits.coordinator.sessions() as db:
        source=await db.scalar(select(MemoryChange).where(MemoryChange.session_id==old.session_id))
    current=await turn('Record the expense I mentioned in the other chat.',datetime(2026,9,3,10))
    result=await capture(current,{'source_id':source.id,'kind':'expense','amount':'100','quote':quote,'description':'Shoes'})
    assert result['status']=='success' and result['occurred_on']=='2026-08-31'
    later=await turn('I spent 200 TRY today.',datetime(2026,9,4,10))
    async with bits.coordinator.sessions() as db:
        future=await db.scalar(select(MemoryChange).where(MemoryChange.session_id==later.session_id))
    with pytest.raises(SolusError):
        await capture(current,{'source_id':future.id,'kind':'expense','amount':'200','quote':future.content,'description':'Later'})
