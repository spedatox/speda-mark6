"""Bounded exact archive reads, sharing Iacon's foreground retrieval allowance."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import select, func, literal_column, or_
from app.models.message import Message
from app.models.session import Session
from app.models.memory_serving import MemoryChange
from app.models.client_compat import ClientSession, HiddenMessage
from app.models.coordination import RoomEvent
from app.services.lexical import fold


def body_text(content):
    if isinstance(content,str): return content
    if isinstance(content,list):
        return '\n'.join(item.get('text','') for item in content if isinstance(item,dict) and item.get('type')=='text')
    return ''


def searchable_body(dialect):
    if dialect=='sqlite':
        sql = "CASE WHEN json_type(messages.content)='text' THEN json_extract(messages.content,'$') WHEN json_type(messages.content)='array' THEN COALESCE((SELECT group_concat(json_extract(j.value,'$.text'),' ') FROM json_each(messages.content) j WHERE json_extract(j.value,'$.type')='text'),'') ELSE '' END"
    else:
        sql = "CASE WHEN json_typeof(messages.content)='string' THEN messages.content #>> '{}' WHEN json_typeof(messages.content)='array' THEN COALESCE((SELECT string_agg(j.value->>'text',' ') FROM json_array_elements(messages.content) j(value) WHERE j.value->>'type'='text'),'') ELSE '' END"
    body=literal_column(sql)
    for original,replacement in zip('İıŞşĞğÜüÖöÇç','iiSsGgUuOoCc'):
        body=func.replace(body,original,replacement)
    return func.lower(body)


async def search_archive(db, context, query, *, after=None, before=None, limit=6, cursor=None):
    terms=list(dict.fromkeys(fold(query).split()))[:8]
    if not terms and not after and not before:
        return {'status':'needs_query','detail':'Supply a literal keyword or an owner-local date range.'}
    tz=ZoneInfo(context.timezone)
    aid=context.agent_id
    aids={aid}
    if context.coordination:
        aids.add(context.coordination.profiles.require(aid).stable_id)
    if aid=='speda': aids.add('warroom')
    stmt=select(Message,Session).join(Session,Message.session_id==Session.id).outerjoin(ClientSession)
    stmt=stmt.where(Session.user_id==context.user_id,Session.agent_id.in_(aids),Message.role.in_(['user','assistant']),
        (ClientSession.hidden.is_(None)) | (ClientSession.hidden.is_(False)),
        Message.id.not_in(select(HiddenMessage.message_id)))
    if context.run_id:
        event=await db.scalar(select(RoomEvent).where(RoomEvent.run_id==context.run_id,
            RoomEvent.room_id==context.room_id,RoomEvent.kind.in_(['user_message','automation_input'])).order_by(RoomEvent.sequence).limit(1))
        if not event: return {'status':'unavailable','detail':'Original run input boundary is unavailable.'}
        stmt=stmt.where(Message.id<=event.payload['message_id'])
    elif context.memory_source_id:
        source=await db.get(MemoryChange,context.memory_source_id)
        if source and source.user_id==context.user_id and source.message_id:
            stmt=stmt.where(Message.id<=source.message_id)
    if cursor is not None: stmt=stmt.where(Message.id<cursor)
    for value,lower in ((after,True),(before,False)):
        if not value: continue
        day=date.fromisoformat(value)
        bound=datetime.combine(day if lower else day+timedelta(days=1),time.min,tzinfo=tz).astimezone(timezone.utc)
        stmt=stmt.where(Message.created_at>=bound) if lower else stmt.where(Message.created_at<bound)
    if after and before and date.fromisoformat(after)>date.fromisoformat(before):
        return {'status':'invalid_date','detail':'The date range is reversed.'}
    if terms:
        body=searchable_body(db.bind.dialect.name)
        stmt=stmt.where(or_(*(body.contains(term,autoescape=True) for term in terms)))
    candidates=list((await db.execute(stmt.order_by(Message.id.desc()).limit(81))).all())
    results=[]
    consumed=None
    for message,session in candidates[:80]:
        consumed=message.id
        text=body_text(message.content)
        folded=fold(text)
        hits=[folded.find(term) for term in terms if term in folded]
        if terms and not hits: continue
        start=max(0,min(hits)-150) if hits else 0
        excerpt=text[start:start+900]
        speaker=next((b.get('agent_id') for b in message.content if isinstance(b,dict) and b.get('type')=='_speaker'),None) if isinstance(message.content,list) else None
        automated=any(isinstance(b,dict) and b.get('type')=='_speda_meta' and b.get('trigger',{}).get('source')=='n8n'
            for b in message.content) if isinstance(message.content,list) else False
        source_id=await db.scalar(select(MemoryChange.id).where(MemoryChange.user_id==context.user_id,
            MemoryChange.message_id==message.id,MemoryChange.revoked.is_(False)).limit(1))
        results.append({'message_id':message.id,'reference':f'message:{message.id}','source_id':source_id,
            'session_id':session.id,'session_title':session.title,'agent_id':speaker or session.agent_id,
            'role':message.role,'attribution':'automated_input_not_owner_assertion' if automated else 'owner_statement' if message.role=='user' else 'assistant_statement_not_owner_assertion',
            'recorded_at':message.created_at.isoformat(),'text':excerpt,'offset_in_plain_text':start,
            'partial_message':start>0 or start+len(excerpt)<len(text)})
        if len(results)>=min(8,max(1,limit)): break
    more=bool(candidates and (consumed!=candidates[-1][0].id or len(candidates)>80))
    return {'status':'partial' if more else 'success','results':results,'has_more':more,
        'next_before_message_id':consumed if more else None,
        'scope':'Literal archive evidence for this agent; bounded results, not an exhaustive semantic search or current personal truth.'}
