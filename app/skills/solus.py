"""Sentinel's deterministic ledger interface, independent of memory maintenance."""
import hashlib
import json
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pydantic import ValidationError
from sqlalchemy import select
from app.skills.base import Skill
from app.models.solus_ledger import SolusBook, SolusCommand
from app.schemas.solus_capture import Capture
from app.services.solus import reports
from app.services.solus.capture import capture, configure_routing
from app.services.solus.store import run_financial_command, source_for
from app.services.solus.commands import SolusError


class SolusAccountingSkill(Skill):
    name = 'solus_accounting'
    read_only = False
    memoize = False
    deferred = False
    restricted_to = frozenset({'sentinel'})
    search_keywords = 'solus accounting finance expense income transfer card installment debt ledger budget receipt'
    description = (
        'Record owner financial occurrences in Solus or read its exact ledger reports. '
        'Use capture with an exact original quote and ordinary transaction facts; the server constructs balanced postings, dates, installments and duplicate identities. '
        'Use books/accounts to resolve names, configure for book/account/card setup, and receipt to reconcile a saved result. '
        'Never compute journal lines, count a card payment as new spending, infer payment from a plan, invent an exchange rate, or combine legacy and Solus totals. '
        'A needs_information result has posted nothing; resolve its specific missing field using owner evidence. This records bookkeeping only and never moves money.')
    input_schema = {'type':'object','properties':{
        'operation':{'type':'string','enum':['books','accounts','events','summary','schedules','plans','statement','forecast','business','positions','reconciliations','capture','configure','receipt']},
        'book_id':{'type':'string','maxLength':120}, 'period':{'type':'string','pattern':r'^\d{4}-\d{2}$'},
        'account_id':{'type':'string','maxLength':36},'cursor':{'type':'string','maxLength':200},
        'command_key':{'type':'string','maxLength':200}, 'facts':Capture.model_json_schema(),
        'configuration':{'type':'object','description':'Native book_create, account_create, card_calendar_put or routing_put fields. routing_put uses book_id, expected_version and account names for payment_account, income_account, card_account, expense_category or income_category; card_payment_allocation may be unallocated or explicitly owner-authorized oldest_billed. No journal commands.'},
        'quote':{'type':'string','minLength':1,'maxLength':12000}},
        'required':['operation'],'additionalProperties':False}

    def available(self, context):
        return context.agent_id=='sentinel' and context.task_role!='worker'

    async def execute(self, args, context):
        if not self.available(context): return '{"status":"rejected","detail":"Only Sentinel may use Solus."}'
        try:
            operation = args['operation']
            if operation=='capture': result = await capture(context,args['facts'])
            elif operation=='configure':
                config = dict(args['configuration'])
                if {'command_key','evidence'} & set(config):
                    raise SolusError('Configuration evidence and command identity are assigned by the server.')
                if config.get('operation')=='routing_put':
                    return json.dumps(await configure_routing(context,config,args['quote']),ensure_ascii=False)
                if config.get('operation') not in {'book_create','account_create','card_calendar_put'}:
                    raise SolusError('Only native book/account/card-calendar configuration is admitted here.')
                async with context.coordination.sessions() as db:
                    source = await source_for(db,context)
                    quote = args['quote']
                    if quote not in source.content: raise SolusError('Setup evidence must match the owner input.')
                    if config['operation']=='book_create':
                        if not config.get('currency'): raise SolusError('Choose the book currency explicitly once during setup.')
                        config.setdefault('timezone',context.timezone)
                    else:
                        book = await db.get(SolusBook,config['book_id'])
                        if not book or book.user_id!=context.user_id: raise SolusError('Book not found.',404)
                        if config['operation']=='account_create': config.setdefault('currency',book.currency)
                        if config['operation']=='card_calendar_put':
                            closes=re.findall(r'(?:closes?|closing(?: day)?|statement date|ekstre kesim(?: günü)?|hesap kesim(?: günü)?)\s*(?:is|on|:)?\s*(\d{1,2})',quote,re.I)
                            offsets=re.findall(r'(?:due(?: after| in)?|kesimden)\s*(?:is|on|:)?\s*(\d{1,2})\s*(?:days?|gün)',quote,re.I)
                            dues=re.findall(r'(?:due(?: day| date)?|son ödeme(?: günü)?)\s*(?:is|on|:)?\s*(\d{1,2})(?!\d|\s*(?:days?|gün))',quote,re.I)
                            if len(set(closes))!=1: raise SolusError('State one explicit statement closing day in the original configuration quote.')
                            if int(closes[0])!=config.get('closing_day'): raise SolusError('Closing day contradicts the original owner instruction.')
                            if offsets:
                                if len(set(offsets))!=1 or int(offsets[0])!=config.get('due_offset') or config.get('due_day') is not None:
                                    raise SolusError('Due offset contradicts the original owner instruction.')
                            elif len(set(dues))!=1 or int(dues[0])!=config.get('due_day') or config.get('due_offset') is not None:
                                raise SolusError('State an unambiguous due day or due offset from the original quote.')
                            stamp=source.created_at.replace(tzinfo=timezone.utc) if source.created_at.tzinfo is None else source.created_at
                            config.setdefault('effective_period',stamp.astimezone(ZoneInfo(book.timezone)).strftime('%Y-%m'))
                    config['evidence']=[{'ref':f'source:{source.id}','quote':quote}]
                    config['command_key']='setup:'+hashlib.sha256(json.dumps({'source':source.id,'configuration':config},sort_keys=True).encode()).hexdigest()
                result = await run_financial_command(context,config)
            else:
                result = await self.read(args,context)
            encoded = json.dumps(result,ensure_ascii=False)
            if len(encoded.encode())>32000:
                return '{"status":"needs_narrower_query","detail":"Report exceeds the bounded tool preview; narrow the period or use the event cursor."}'
            return encoded
        except (SolusError,ValueError,KeyError,ValidationError) as exc:
            return json.dumps({'status':'rejected','detail':str(exc)[:1200],'posted':False},ensure_ascii=False)

    async def read(self, args, context):
        from sqlalchemy.ext.asyncio import AsyncSession
        async with AsyncSession(bind=context.db.bind,expire_on_commit=False) as db:
            op = args['operation']
            if op=='books': return await reports.books(db,context.user_id)
            if op=='receipt':
                saved = await db.scalar(select(SolusCommand).where(SolusCommand.user_id==context.user_id,
                    SolusCommand.command_key==args['command_key']))
                return dict(saved.receipt) if saved else {'status':'not_found','meaning':'No committed receipt; this does not authorize a new occurrence.'}
            book = await db.get(SolusBook,args['book_id'])
            if not book or book.user_id!=context.user_id: raise SolusError('Book not found.',404)
            # Current-period reads use the admitted turn's clock, not a later
            # provider/tool execution time across midnight.
            stamp = datetime.fromisoformat(context.prompt_datetime.replace('Z','+00:00')) if context.prompt_datetime else datetime.now(timezone.utc)
            if stamp.tzinfo is None: stamp = stamp.replace(tzinfo=timezone.utc)
            period = args.get('period') or stamp.astimezone(ZoneInfo(book.timezone)).strftime('%Y-%m')
            if op=='accounts':
                from app.models.solus_routing import SolusRouting
                routing = await db.get(SolusRouting,book.id)
                return {'accounts':await reports.accounts(db,context.user_id,book.id),
                    'routing':{'version':routing.version,'settings':routing.settings} if routing else {'version':0,'settings':{}}}
            if op=='events': return await reports.events(db,context.user_id,book.id,before=args.get('cursor'))
            if op=='schedules': return await reports.schedules(db,context.user_id,book.id,period=period,after=args.get('cursor'))
            if op=='plans': return await reports.plans(db,context.user_id,book.id)
            if op=='summary': return await reports.summary(db,context.user_id,book.id,period)
            if op=='forecast': return await reports.forecast(db,context.user_id,book.id,period)
            if op=='statement': return await reports.statement(db,context.user_id,book.id,args['account_id'],period)
            if op=='business': return await reports.business_report(db,context.user_id,book.id,period)
            if op=='reconciliations': return await reports.reconciliations(db,context.user_id,book.id)
            if op=='positions':
                from app.services.solus.investments import positions
                return await positions(db,context.user_id,book.id,period)
            raise SolusError('Unknown Solus operation.')
