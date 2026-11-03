"""Deterministic ordinary transaction templates above the exact ledger."""
import hashlib
import re
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import select
from app.models.solus_ledger import SolusBook, SolusAccount
from app.models.solus_routing import SolusRouting, SolusRoutingRevision
from app.schemas.solus_capture import Capture
from app.services.solus.money import minor, money, currency_scale
from app.services.solus.store import run_financial_command, source_for
from app.services.solus.commands import SolusError


class NeedsInformation(ValueError):
    def __init__(self, field, detail, candidates=None):
        self.field, self.detail, self.candidates = field, detail, candidates or []
        super().__init__(detail)


def normalized_number(token):
    if ',' in token and '.' in token:
        decimal = ',' if token.rfind(',') > token.rfind('.') else '.'
        grouping = '.' if decimal == ',' else ','
        return token.replace(grouping,'').replace(decimal,'.')
    for separator in (',','.'):
        if separator in token:
            parts = token.split(separator)
            if len(parts)>2 or len(parts[-1])==3:
                if all(len(part)==3 for part in parts[1:]): return ''.join(parts)
                raise NeedsInformation('amount','Ambiguous number separators; use an explicit decimal amount.')
            return token.replace(separator,'.')
    return token


def source_amount(quote, amount, currency):
    target = minor(amount,currency)
    matches = []
    for match in re.finditer(r'(?<![\w])\d+(?:[.,]\d+)*(?![\w])',quote):
        try:
            if minor(normalized_number(match.group()),currency)==target: matches.append(match.start())
        except (ValueError,NeedsInformation): pass
    if len(matches)!=1:
        raise NeedsInformation('amount','The amount must match one unambiguous numeric occurrence in the original quote.')
    return target,matches[0]


def occurrence_date(phrase, quote, captured_at, tz):
    timestamp = captured_at.replace(tzinfo=timezone.utc) if captured_at.tzinfo is None else captured_at
    today = timestamp.astimezone(ZoneInfo(tz)).date()
    text = quote.casefold()
    if not phrase:
        relative = re.findall(r'\b(today|bugün|yesterday|dün|tomorrow|yarın)\b',text)
        weekdays = r'monday|tuesday|wednesday|thursday|friday|saturday|sunday|pazartesi|salı|çarşamba|perşembe|cuma|cumartesi|pazar'
        relative += re.findall(r'\b(?:last|geçen) (?:'+weekdays+r')\b',text)
        relative += re.findall(r'\b\d{1,4} (?:days? ago|gün önce)\b',text)
        explicit = re.findall(r'\b\d{4}-\d{2}-\d{2}\b|\b\d{2}\.\d{2}\.\d{4}\b',text)
        found = set(relative+explicit)
        if len(found)==1: phrase = found.pop()
    if phrase:
        value = phrase.strip().casefold()
        if value not in text: raise NeedsInformation('date_phrase','Date wording must occur in the original evidence.')
        offsets = {'today':0,'bugün':0,'yesterday':-1,'dün':-1,'tomorrow':1,'yarın':1}
        if value in offsets: return today+timedelta(days=offsets[value])
        names=['monday','tuesday','wednesday','thursday','friday','saturday','sunday',
            'pazartesi','salı','çarşamba','perşembe','cuma','cumartesi','pazar']
        if value.startswith(('last ','geçen ')) and value.split(' ',1)[1] in names:
            target=names.index(value.split(' ',1)[1])%7
            return today-timedelta(days=(today.weekday()-target)%7 or 7)
        ago=re.fullmatch(r'(\d{1,4}) (?:days? ago|gün önce)',value)
        if ago and int(ago[1])<=3650: return today-timedelta(days=int(ago[1]))
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}',value): return date.fromisoformat(value)
        if re.fullmatch(r'\d{2}\.\d{2}\.\d{4}',value): return datetime.strptime(value,'%d.%m.%Y').date()
        raise NeedsInformation('date_phrase','Use an explicit ISO date, DD.MM.YYYY, today or yesterday; do not guess ambiguous dates.')
    if re.search(r'\b(yesterday|tomorrow|dün|yarın|last|geçen|önce|ago)\b|\d{4}-\d{2}-\d{2}|\d{1,2}[./]\d{1,2}[./]\d{2,4}',text):
        raise NeedsInformation('date_phrase','The source contains a time qualifier; resolve that wording before posting.')
    return today


def account(rows, selector, field, types, currency, *, quote='', default=None):
    available = [a for a in rows if a.account_type in types and a.currency==currency and not a.closed and a.report_role=='financial']
    if selector is None:
        named = [a for a in available if re.search(r'(?<!\w)'+re.escape(a.name)+r'(?!\w)',quote,re.I)]
        selector = named[0].id if len(named)==1 else default
    if selector:
        selected = [a for a in available if a.id==selector or a.name.casefold()==selector.casefold()]
    else:
        selected = []
    if len(selected)!=1:
        raise NeedsInformation(field,'Select an existing account by its exact name; no balance or payment method was assumed.',
            [{'id':a.id,'name':a.name,'currency':a.currency,'type':a.account_type} for a in available])
    if field in {'from_account','to_account'} and selected[0].name.casefold() not in quote.casefold() and selected[0].id!=default:
        raise NeedsInformation(field,'The payment account is not named in the source or configured as an owner default.',
            [{'id':a.id,'name':a.name} for a in available])
    return selected[0]


async def capture(context, facts):
    facts = Capture.model_validate(facts)
    if context.agent_id!='sentinel' or context.task_role=='worker':
        raise SolusError('Only Sentinel can record conversation-backed financial events.',403)
    source_id = None
    try:
        async with context.db.bind.connect() as connection:
            # Independent snapshot, never commit a caller's pending chat/tool data.
            from sqlalchemy.ext.asyncio import AsyncSession
            async with AsyncSession(bind=connection,expire_on_commit=False) as db:
                source = await source_for(db,context,facts.source_id,attachment_id=facts.attachment_id)
                source_id = source.id
                starts = [m.start() for m in re.finditer(re.escape(facts.quote),source.content)]
                offset = facts.quote_start if facts.quote_start is not None else (starts[0] if len(starts)==1 else None)
                if offset is None or source.content[offset:offset+len(facts.quote)]!=facts.quote:
                    raise NeedsInformation('quote','Quote must identify one exact original occurrence; supply its character offset if repeated.')
                books = list(await db.scalars(select(SolusBook).where(SolusBook.user_id==context.user_id)))
                selected = [b for b in books if facts.book in {b.id,b.name}] if facts.book else [b for b in books if b.kind=='personal']
                if len(selected)!=1: raise NeedsInformation('book','Choose one book; personal and business totals must remain separate.',
                    [{'id':b.id,'name':b.name,'kind':b.kind} for b in books])
                book = selected[0]
                currency = facts.currency or book.currency
                amount,amount_offset = source_amount(facts.quote,facts.amount,currency)
                counts = set(int(value) for value in re.findall(r'(?<!\d)(\d{1,3})\s*(?:installments?|taksit)\b',facts.quote,re.I))
                if len(counts)>1: raise NeedsInformation('installments','Several installment counts appear in the source; identify one occurrence.')
                installments = next(iter(counts)) if counts else facts.installments
                if installments<1 or installments>600: raise NeedsInformation('installments','Installment count must be between 1 and 600.')
                if facts.installments!=1 and facts.installments!=installments:
                    raise NeedsInformation('installments','The supplied count contradicts the original quote.')
                if installments>1 and facts.kind not in {'card_purchase','bill','invoice','loan_disbursement'}:
                    raise NeedsInformation('kind','This is installment spending; resolve its payment method before posting.')
                if facts.installments>1 and not counts:
                    raise NeedsInformation('installments','The original quote must explicitly state the installment count.')
                number_tail = facts.quote[amount_offset:]
                per_installment = bool(re.match(r'\d+(?:[.,]\d+)*\s*(?:TRY|TL|USD|EUR|GBP|₺)?\s*(?:each|per installment|her taksit)',number_tail,re.I)
                    or re.search(r'(?:each installment|per installment|her taksit|taksit başına)\s*(?:TRY|TL|USD|EUR|GBP|₺)?\s*$',facts.quote[:amount_offset],re.I))
                if per_installment:
                    if installments==1: raise NeedsInformation('installments','Per-installment amount needs the original total installment count.')
                    amount = minor(money(amount*installments,currency_scale(currency)),currency)
                observed = {m.group().upper() for m in re.finditer(r'\b(?:TRY|USD|EUR|GBP|JPY|KWD|CHF|CAD|AUD)\b',facts.quote,re.I)}
                if observed and observed!={currency}: raise NeedsInformation('currency','The source currency differs or contains multiple currencies; do not infer an exchange rate.')
                if ('₺' in facts.quote or re.search(r'\bTL\b',facts.quote,re.I)) and currency!='TRY':
                    raise NeedsInformation('currency','The original amount is denominated in TRY.')
                obligation = facts.kind in {'bill','invoice','loan_disbursement'}
                denied = r'\b(will|plan|planned|never|didn.t|haven.t)\b|harcayaca|harcamadım|almadım' if obligation else r'\b(will|plan|planned|unpaid|not|never|didn.t|haven.t|henüz|yarın)\b|ödeyece|harcayaca|ödemedim|harcamadım|ödenmedi|almadım'
                denied += r'|\b(estimate|estimated|expected)\b|not (?:issued|received|owed|incurred|borrowed)|bekleniyor|planlı'
                if re.search(denied,facts.quote,re.I):
                    raise NeedsInformation('occurrence','This quote describes a plan or an unpaid/negated event. No actual journal was posted; use plans or obligations.')
                occurrence_quote = facts.quote
                if facts.due_date_phrase and not facts.date_phrase:
                    occurrence_quote = occurrence_quote.replace(facts.due_date_phrase,'[due date]',1)
                occurred_on = occurrence_date(facts.date_phrase,occurrence_quote,source.created_at,book.timezone)
                due_on = occurrence_date(facts.due_date_phrase,facts.quote,source.created_at,book.timezone) if facts.due_date_phrase else None
                if due_on and not obligation: raise NeedsInformation('due_date_phrase','Due dates belong to obligations; they do not change actual spending dates.')
                if obligation and installments>1 and not due_on:
                    raise NeedsInformation('due_date_phrase','Installment obligations need their original first due date.')
                if facts.document_number and facts.document_number not in facts.quote:
                    raise NeedsInformation('document_number','Use the document reference printed in the original evidence.')
                if facts.attachment_id and (not re.search(r'\b\d{4}-\d{2}-\d{2}\b|\b\d{2}\.\d{2}\.\d{4}\b',occurrence_quote)
                        or (facts.date_phrase and not re.fullmatch(r'\d{4}-\d{2}-\d{2}|\d{2}\.\d{2}\.\d{4}',facts.date_phrase))):
                    raise NeedsInformation('date_phrase','A document needs its explicit original transaction date; upload time and relative wording cannot establish it.')
                source_day = source.created_at.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(book.timezone)).date() if source.created_at.tzinfo is None else source.created_at.astimezone(ZoneInfo(book.timezone)).date()
                if occurred_on>source_day: raise NeedsInformation('occurrence','Future transactions remain plans, not actual spending or income.')
                if facts.kind=='expense' and re.search(r'credit card (?:bill|payment|debt)|kredi kartı (?:bor|ödem)',facts.quote,re.I):
                    raise NeedsInformation('kind','A card debt payment is a transfer against a liability, not another expense. Use card_payment.')
                if facts.kind=='income' and re.search(r'\b(refund|reimbursement|borrowed|loan disbursement)\b|iade|borç ald|kendi hesaplarım',facts.quote,re.I):
                    raise NeedsInformation('kind','Refunds, borrowed principal and movements between your own accounts are not new earned income.')
                rows = list(await db.scalars(select(SolusAccount).where(SolusAccount.book_id==book.id)))
                if facts.kind=='expense' and any(re.search(r'(?<!\w)'+re.escape(a.name)+r'(?!\w)',facts.quote,re.I)
                        for a in rows if a.account_type=='liability'):
                    raise NeedsInformation('kind','The source names a liability account; distinguish a card purchase from repayment.')
                routing = await db.get(SolusRouting,book.id)
                defaults = routing.settings if routing else {}
                if facts.kind=='expense':
                    debit = account(rows,facts.category,'category',{'expense'},currency,default=defaults.get('expense_category'))
                    credit = account(rows,facts.from_account,'from_account',{'asset'},currency,quote=facts.quote,default=defaults.get('payment_account'))
                elif facts.kind=='income':
                    debit = account(rows,facts.to_account,'to_account',{'asset'},currency,quote=facts.quote,default=defaults.get('income_account'))
                    credit = account(rows,facts.category,'category',{'income'},currency,default=defaults.get('income_category'))
                elif facts.kind=='card_purchase':
                    debit = account(rows,facts.category,'category',{'expense'},currency,default=defaults.get('expense_category'))
                    credit = account(rows,facts.from_account,'from_account',{'liability'},currency,quote=facts.quote,default=defaults.get('card_account'))
                elif facts.kind=='bill':
                    debit = account(rows,facts.category,'category',{'expense'},currency,default=defaults.get('expense_category'))
                    credit = account(rows,facts.to_account,'to_account',{'liability'},currency,quote=facts.quote)
                elif facts.kind=='invoice':
                    debit = account(rows,facts.to_account,'to_account',{'asset'},currency,quote=facts.quote)
                    credit = account(rows,facts.category,'category',{'income'},currency,default=defaults.get('income_category'))
                elif facts.kind=='loan_disbursement':
                    debit = account(rows,facts.to_account,'to_account',{'asset'},currency,quote=facts.quote)
                    credit = account(rows,facts.from_account,'from_account',{'liability'},currency,quote=facts.quote)
                elif facts.kind=='refund':
                    debit = account(rows,facts.to_account,'to_account',{'asset','liability'},currency,quote=facts.quote)
                    credit = account(rows,facts.category,'category',{'expense'},currency,default=defaults.get('expense_category'))
                else:
                    debit = account(rows,facts.to_account,'to_account',{'liability'} if facts.kind in {'card_payment','bill_payment'} else {'asset'},currency,
                        quote=facts.quote,default=defaults.get('card_account') if facts.kind=='card_payment' else None)
                    credit = account(rows,facts.from_account,'from_account',{'asset'},currency,quote=facts.quote,default=defaults.get('payment_account'))
                if debit.id==credit.id: raise NeedsInformation('to_account','A transfer requires two different accounts.')
                source_hash = source.content_hash
                reference = getattr(source,'reference',f'source:{source_id}')
                anchor = offset+amount_offset
                # Stable source excerpt, unaffected by a model changing its
                # description or widening the evidence quote on a later retry.
                evidence_quote = source.content[max(0,anchor-100):anchor+200]
        # One source monetary token owns one occurrence, regardless of changed
        # tool IDs, descriptions, later model retries or a wider evidence quote.
        occurrence = f'source:{source_id}:amount:{offset+amount_offset}'
        key = 'capture:'+hashlib.sha256(f'{context.user_id}:{occurrence}'.encode()).hexdigest()
        value = money(amount,currency_scale(currency))
        payload = {'operation':'post','command_key':key,'book_id':book.id,'occurrence_key':occurrence,
            'kind':'debt_payment' if facts.kind=='card_payment' else facts.kind,
            'description':evidence_quote[:500],'occurred_on':occurred_on.isoformat(),
            'lines':[{'account_id':debit.id,'side':'debit','amount':value},
                     {'account_id':credit.id,'side':'credit','amount':value}],
            'evidence':[{'ref':reference,'quote':evidence_quote}]}
        if facts.kind=='card_purchase': payload['installments']=installments
        if obligation and due_on:
            payload.update(first_due_on=due_on.isoformat(),installments=installments,schedule_amount=value,schedule_currency=currency)
        if facts.document_number: payload['document']={'number':facts.document_number}
        local = replace(context,extra={**context.extra,'solus_template':True,
            'solus_routing_version':routing.version if routing else 0,
            'solus_settle_document':facts.document_number if facts.kind in {'bill_payment','invoice_payment'} else None,
            'solus_allocation_policy':defaults.get('card_payment_allocation','unallocated') if facts.kind=='card_payment' else None})
        result = await run_financial_command(local,payload)
        return {**result,'source_id':source_id,'source_sha256':source_hash,'occurred_on':occurred_on.isoformat(),
            'amount':value,'currency':currency,'accounting_kind':payload['kind'],'monetary_side_effect':'ledger_record_only',
            'schedule_status':'created' if due_on or facts.kind=='card_purchase' else 'not_supplied',
            'settlement_status':('matched' if result.get('settlement',{}).get('allocated') not in {None,'0.00'} else 'unmatched') if facts.kind in {'invoice_payment','bill_payment'} else None}
    except NeedsInformation as exc:
        return {'status':'needs_information','source_id':source_id,'field':exc.field,'detail':exc.detail,
            'candidates':exc.candidates,'posted':False}


async def configure_routing(context, config, quote):
    from app.services.solus.store import writer_fence, validate_agent
    from sqlalchemy.ext.asyncio import async_sessionmaker
    if set(config)-{'operation','book_id','expected_version','payment_account','income_account','card_account','expense_category','income_category','card_payment_allocation'}:
        raise SolusError('Unknown bookkeeping default field.')
    if not re.search(r'\b(default|defaults|varsayılan|kullan)\b|use .+ for',quote,re.I):
        raise SolusError('Bookkeeping defaults require an explicit owner instruction; ordinary purchases cannot change defaults.')
    sessions = async_sessionmaker(context.db.bind,expire_on_commit=False)
    async with sessions() as db, db.begin():
        await writer_fence(db,context.user_id)
        await validate_agent(db,context)
        source = await source_for(db,context)
        if quote not in source.content: raise SolusError('Default-setting quote must match original owner evidence.')
        book = await db.get(SolusBook,config['book_id'])
        if not book or book.user_id!=context.user_id: raise SolusError('Book not found.',404)
        row = await db.get(SolusRouting,book.id)
        settings = dict(row.settings) if row else {}
        roles = {'payment_account':'asset','income_account':'asset','card_account':'liability',
            'expense_category':'expense','income_category':'income'}
        for key,kind in roles.items():
            if key not in config: continue
            choices = list(await db.scalars(select(SolusAccount).where(SolusAccount.book_id==book.id,
                SolusAccount.account_type==kind,SolusAccount.closed.is_(False),SolusAccount.report_role=='financial')))
            matches = [a for a in choices if config[key] in {a.id,a.name} and a.name.casefold() in quote.casefold()]
            if len(matches)!=1: raise SolusError('Each default must name one existing account in the original instruction.')
            settings[key]=matches[0].id
        if 'card_payment_allocation' in config:
            policy = config['card_payment_allocation']
            if policy not in {'unallocated','oldest_billed'}: raise SolusError('Unknown card payment allocation policy.')
            if policy=='oldest_billed' and not re.search(r'oldest billed|en eski (?:ekstre|fatura)',quote,re.I):
                raise SolusError('Oldest-billed allocation requires that explicit owner instruction.')
            settings['card_payment_allocation']=policy
        if not any(key in config for key in roles) and 'card_payment_allocation' not in config:
            raise SolusError('At least one default is required.')
        # A repeated unchanged request is an acknowledgement, not a new revision.
        if row and settings==row.settings:
            return {'status':'success','book_id':book.id,'version':row.version,'settings':settings,'duplicate':True}
        if type(config.get('expected_version')) is not int or config['expected_version']!=(row.version if row else 0):
            raise SolusError('Default configuration changed; reread its version.',409)
        if row is None:
            row = SolusRouting(book_id=book.id,version=0)
            db.add(row)
        row.settings,row.version,row.updated_at = settings,row.version+1,datetime.now(timezone.utc).replace(tzinfo=None)
        row.evidence=[{'ref':f'source:{source.id}','quote':quote,'source_sha256':source.content_hash}]
        db.add(SolusRoutingRevision(book_id=book.id,version=row.version,settings=dict(settings),evidence=row.evidence))
        return {'status':'success','book_id':book.id,'version':row.version,'settings':settings}
