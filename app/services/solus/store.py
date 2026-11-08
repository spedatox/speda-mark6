"""Atomic exact-ledger authority. No model admission or legacy memory writes."""
import hashlib
import json
import re
import time
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker
from app.models.solus_ledger import SolusCommand, SolusEvent, SolusPosting, SolusScheduleItem
from app.models.memory_serving import MemoryChange
from app.models.coordination import RoomEvent, CoordinationRun, AgentStep, Room, RoomAttachment
from app.models.user import User
from app.schemas.solus import parse_command
from app.services.solus.commands import apply, SolusError, effective_settled, effective_dates


async def source_for(db, context, source_id=None, attachment_id=None, attachment_sha=None):
    """Original owner evidence, bounded by this run's admitted input, never a later queued turn."""
    if context.task_role == 'worker' or context.triggered_by == 'n8n' or not context.run_id:
        raise SolusError('Financial recording requires an admitted owner conversation.', 403)
    event = await db.scalar(select(RoomEvent).where(RoomEvent.run_id == context.run_id,
        RoomEvent.room_id == context.room_id, RoomEvent.kind == 'user_message').order_by(RoomEvent.sequence).limit(1))
    if event is None: raise SolusError('Original owner input is unavailable.', 403)
    boundary = event.payload['message_id']
    if attachment_id or attachment_sha:
        from types import SimpleNamespace
        admitted = [item['id'] for item in event.payload.get('attachments',[])]
        stmt = select(RoomAttachment).where(RoomAttachment.id.in_(admitted),RoomAttachment.room_id==context.room_id)
        stmt = stmt.where(RoomAttachment.id==attachment_id) if attachment_id else stmt.where(RoomAttachment.sha256==attachment_sha)
        attachment = await db.scalar(stmt.limit(1))
        original = await db.scalar(select(MemoryChange).where(MemoryChange.user_id==context.user_id,
            MemoryChange.session_id==context.session_id,MemoryChange.message_id==boundary,
            MemoryChange.source_role=='owner',MemoryChange.revoked.is_(False)))
        if not attachment or not original or not attachment.extracted or hashlib.sha256(attachment.original).hexdigest()!=attachment.sha256:
            raise SolusError('Admitted original document text is unavailable; image-only evidence requires verified extraction.',403)
        return SimpleNamespace(id='attachment:'+attachment.sha256,content=attachment.extracted,
            content_hash=attachment.sha256,created_at=original.created_at,
            reference='attachment-sha256:'+attachment.sha256,authority='owner_uploaded_document')
    stmt = select(MemoryChange).where(MemoryChange.user_id == context.user_id,
        MemoryChange.source_role == 'owner',
        MemoryChange.message_id <= boundary, MemoryChange.revoked.is_(False))
    stmt = stmt.where(MemoryChange.id == source_id) if source_id else stmt.where(
        MemoryChange.session_id == context.session_id, MemoryChange.message_id == boundary)
    row = await db.scalar(stmt)
    if row is None or hashlib.sha256(row.content.encode()).hexdigest() != row.content_hash:
        raise SolusError('Original owner evidence is unavailable or changed.', 403)
    return row


async def validate_agent(db, context):
    run = await db.get(CoordinationRun,context.run_id)
    room = await db.get(Room,context.room_id)
    step = await db.get(AgentStep,context.step_id)
    if (not context.coordination or not run or not room or not step or run.user_id!=context.user_id
            or run.room_id!=room.id or room.user_id!=context.user_id or room.session_id!=context.session_id
            or step.run_id!=run.id or step.agent_id!=context.agent_id or step.status!='running'
            or run.cancelled or run.status!='running' or run.deadline<=time.time()
            or run.executor!=context.coordination.identity or run.lease_until<=time.time()):
        raise SolusError('Financial recording is fenced: the admitted contribution is no longer active.',409)


async def resolve_evidence(db, context, supplied):
    result = []
    for item in supplied:
        match = re.fullmatch(r'source:(\d+)', item['ref'])
        file_match = re.fullmatch(r'attachment-sha256:([a-f0-9]{64})',item['ref'])
        if match: row = await source_for(db, context, int(match[1]))
        elif file_match: row = await source_for(db,context,attachment_sha=file_match[1])
        else: raise SolusError('Use an original Iacon source or admitted document, not remembered or assistant prose.')
        if item['quote'] not in row.content: raise SolusError('Evidence quote does not match its original source.')
        result.append({**item,'source_sha256':row.content_hash,'source_authority':getattr(row,'authority','owner'),
            'quoted_text_sha256':hashlib.sha256(row.content.encode()).hexdigest()})
    if not result: raise SolusError('Financial assertions require original owner evidence.')
    return result


async def writer_fence(db, user_id):
    if db.bind.dialect.name == 'postgresql':
        # Serialize a single owner's ledger decisions without locking their chat row.
        await db.execute(text("SELECT pg_advisory_xact_lock(1936682101, :owner)"), {"owner": user_id})
        return
    if db.bind.dialect.name != 'sqlite':
        raise SolusError('Unsupported Solus ledger store.', 409)
    # A database write lock precedes all receipt/source/account reads. It also
    # works with the native engine's explicit BEGIN event (no nested BEGIN).
    await db.execute(update(User).where(User.id == user_id).values(name=User.name))
    from app.services.solus.guards import install_guards
    connection = await db.connection()
    await connection.run_sync(install_guards)


async def prior(db, user_id, key, fingerprint, canonical):
    saved = await db.scalar(select(SolusCommand).where(SolusCommand.user_id == user_id,
        SolusCommand.command_key == key))
    if saved and saved.payload_hash != fingerprint:
        try: equivalent = parse_command(saved.payload).model_dump(mode='json') == canonical
        except ValueError: equivalent = False
        if not equivalent:
            raise SolusError('Idempotency key belongs to a different command; nothing changed.', 409)
    return dict(saved.receipt) if saved else None


async def allocate_card_payment(db, command):
    """Owner-selected oldest billed allocation, computed inside the writer fence."""
    from app.schemas.solus import Allocation
    from app.services.solus.money import minor, money, currency_scale
    debit = next(line for line in command.lines if line.side=='debit')
    from app.models.solus_ledger import SolusAccount
    card = await db.get(SolusAccount,debit.account_id)
    available = minor(debit.amount,card.currency)
    original = available
    reversed_ids = select(SolusEvent.reverses_id).where(SolusEvent.reverses_id.is_not(None),SolusEvent.posted.is_(True))
    items = list(await db.scalars(select(SolusScheduleItem).join(SolusEvent,
        SolusEvent.id==SolusScheduleItem.event_id).join(SolusPosting,SolusPosting.event_id==SolusEvent.id)
        .where(SolusScheduleItem.book_id==command.book_id,SolusPosting.account_id==card.id,
            SolusPosting.amount_minor<0,SolusEvent.posted.is_(True),SolusEvent.id.not_in(reversed_ids),
            SolusEvent.occurred_on<=command.occurred_on)))
    eligible=[]
    for item in items:
        billed,due=await effective_dates(db,item,account_id=card.id)
        if billed and billed<=command.occurred_on:
            eligible.append((billed,due,item.event_id,item.sequence,item))
    allocations=[]
    for *_,item in sorted(eligible,key=lambda value:value[:4]):
        remaining=item.amount_minor-await effective_settled(db,item.id)
        value=min(available,max(0,remaining))
        if value:
            allocations.append(Allocation(item_id=item.id,amount=money(value,card.scale)))
            available-=value
        if not available: break
    return command.model_copy(update={'settlements':allocations}), {
        'policy':'oldest_billed','allocated':money(original-available,card.scale),
        'unallocated':money(available,card.scale),'currency':card.currency}


async def allocate_document_payment(db, command, number):
    from app.schemas.solus import Allocation
    from app.services.solus.money import minor, money
    from app.models.solus_ledger import SolusAccount
    kind = 'bill' if command.kind=='bill_payment' else 'invoice'
    reversed_ids = select(SolusEvent.reverses_id).where(SolusEvent.reverses_id.is_not(None),SolusEvent.posted.is_(True))
    events = list(await db.scalars(select(SolusEvent).where(SolusEvent.book_id==command.book_id,
        SolusEvent.kind==kind,SolusEvent.posted.is_(True),SolusEvent.id.not_in(reversed_ids),
        SolusEvent.details['document']['number'].as_string()==number)))
    if len(events)!=1: raise SolusError('Document reference must identify one unreversed obligation in this book; nothing was posted.',409)
    event=events[0]
    if event.occurred_on>command.occurred_on: raise SolusError('A settlement cannot precede its original obligation.')
    principal_line = next(line for line in command.lines if line.side==('debit' if kind=='bill' else 'credit'))
    principal = await db.get(SolusAccount,principal_line.account_id)
    original_postings=list(await db.scalars(select(SolusPosting).where(SolusPosting.event_id==event.id)))
    expected_sign=-1 if kind=='bill' else 1
    if not any(line.account_id==principal.id and line.amount_minor*expected_sign>0 for line in original_postings):
        raise SolusError('Payment must reduce this document\'s original payable or receivable account.')
    value = minor(principal_line.amount,principal.currency)
    available=value
    items=list(await db.scalars(select(SolusScheduleItem).where(SolusScheduleItem.event_id==event.id)
        .order_by(SolusScheduleItem.sequence)))
    allocations=[]
    for item in items:
        amount=min(available,max(0,item.amount_minor-await effective_settled(db,item.id)))
        if amount:
            allocations.append(Allocation(item_id=item.id,amount=money(amount,item.scale)))
            available-=amount
        if not available: break
    return command.model_copy(update={'settlements':allocations}), {
        'policy':'document_reference','document_number':number,'event_id':event.id,
        'allocated':money(value-available,principal.scale),'unallocated':money(available,principal.scale),
        'currency':principal.currency}


async def run_financial_command(context, payload):
    command = parse_command(payload)
    canonical = command.model_dump(mode='json')
    fingerprint = hashlib.sha256(json.dumps(canonical,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    if context.agent_id != 'owner':
        if context.agent_id != 'sentinel': raise SolusError('Only Sentinel may record owner financial evidence.', 403)
        if command.operation not in {'book_create','account_create','card_calendar_put','card_statement_put'} and not context.extra.get('solus_template'):
            raise SolusError('Use the deterministic record interface; free-form model journals are not admitted.', 403)
    sessions = async_sessionmaker(context.db.bind, expire_on_commit=False)
    async with sessions() as db:
        cached = await prior(db, context.user_id, command.command_key, fingerprint, canonical)
        if cached: return cached
    try:
        async with sessions() as db, db.begin():
            await writer_fence(db, context.user_id)
            cached = await prior(db, context.user_id, command.command_key, fingerprint, canonical)
            if cached: return cached
            if context.agent_id == 'owner':
                evidence = [{'ref':'solus-command:'+command.command_key, 'quote':json.dumps(canonical,ensure_ascii=False),
                    'source_sha256':fingerprint,'source_authority':'owner'}]
            else:
                await validate_agent(db,context)
                evidence = await resolve_evidence(db, context, [e.model_dump() for e in command.evidence])
                if 'solus_routing_version' in context.extra:
                    from app.models.solus_routing import SolusRouting
                    routing = await db.get(SolusRouting,command.book_id)
                    if (routing.version if routing else 0)!=context.extra['solus_routing_version']:
                        raise SolusError('Bookkeeping defaults changed before posting; reread the accounts.',409)
            settlement = None
            if context.extra.get('solus_settle_document') and command.operation=='post':
                command,settlement = await allocate_document_payment(db,command,context.extra['solus_settle_document'])
                canonical = command.model_dump(mode='json')
            if context.extra.get('solus_allocation_policy') and command.operation=='post' and command.kind=='debt_payment':
                if context.extra['solus_allocation_policy']=='oldest_billed':
                    command,settlement = await allocate_card_payment(db,command)
                    canonical = command.model_dump(mode='json')
                else:
                    settlement = {'policy':'unallocated','detail':'Liability payment recorded; scheduled installments remain unmatched until reconciliation.'}
            result = await apply(db, command, context, evidence)
            receipt = {'status':'success','command_key':command.command_key,**result}
            if 'solus_routing_version' in context.extra:
                receipt['routing_version']=context.extra['solus_routing_version']
            if settlement is not None: receipt['settlement']=settlement
            db.add(SolusCommand(user_id=context.user_id,command_key=command.command_key,
                payload_hash=fingerprint,payload=canonical,receipt=receipt,
                actor=context.agent_id,request_id=context.request_id))
            # Financial truth stays in this ledger. Iacon is not a second balance
            # writer, and no paid judge or legacy finance projection runs here.
            return receipt
    except IntegrityError as exc:
        raise SolusError('Accounting identity or journal constraint failed; nothing saved.',409) from exc


async def resolve_financial_command(context, command_key):
    if context.agent_id != 'owner' or not 8 <= len(command_key) <= 200:
        raise SolusError('Only the owner can resolve an uncertain command key.',403)
    sessions = async_sessionmaker(context.db.bind,expire_on_commit=False)
    async with sessions() as db, db.begin():
        await writer_fence(db,context.user_id)
        existing = await db.scalar(select(SolusCommand).where(SolusCommand.user_id == context.user_id,
            SolusCommand.command_key == command_key))
        if existing: return dict(existing.receipt)
        payload = {'operation':'cancel_uncertain','command_key':command_key}
        receipt = {'status':'cancelled','command_key':command_key}
        db.add(SolusCommand(user_id=context.user_id,command_key=command_key,
            payload_hash=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest(),
            payload=payload,receipt=receipt,actor='owner',request_id=context.request_id))
        return receipt
