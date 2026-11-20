"""Bounded exact archive reads, sharing Iacon's foreground retrieval allowance."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import any_, select, func, literal_column, or_
from sqlalchemy.dialects.postgresql import array
from app.models.message import Message
from app.models.session import Session
from app.models.memory_serving import MemoryChange
from app.models.client_compat import ClientSession, HiddenMessage
from app.models.coordination import AgentStep, Room, RoomEvent
from app.models.iacon import IaconSourceRetirement
from app.services.lexical import fold


def body_text(content):
    if isinstance(content,str): return content
    if isinstance(content,list):
        return '\n'.join(item.get('text','') for item in content if isinstance(item,dict) and item.get('type')=='text')
    return ''


def searchable_body(dialect, column="messages.content"):
    if dialect=='sqlite':
        sql = "CASE WHEN json_type(messages.content)='text' THEN json_extract(messages.content,'$') WHEN json_type(messages.content)='array' THEN COALESCE((SELECT group_concat(json_extract(j.value,'$.text'),' ') FROM json_each(messages.content) j WHERE json_extract(j.value,'$.type')='text'),'') ELSE '' END"
    else:
        # PostgreSQL JSON retains escaped zero bytes but cannot extract them
        # as SQL text. Rewrite only real escapes in the searchable projection.
        safe_json = rf"""CASE WHEN strpos({column}::text, chr(92) || 'u0000') > 0
            THEN regexp_replace({column}::text,
                $zero$(?<!\\)((?:\\\\)*)\\u0000$zero$, $safe$\1\\u0001$safe$, 'g')::json
            ELSE {column} END"""
        sql = f"""(SELECT CASE WHEN json_typeof(body)='string' THEN body #>> '{{}}'
            WHEN json_typeof(body)='array' THEN COALESCE((SELECT string_agg(j.value->>'text',' ')
                FROM json_array_elements(body) j(value) WHERE j.value->>'type'='text'),'')
            ELSE '' END FROM (VALUES ({safe_json})) AS searchable(body))"""
    body=literal_column(sql)
    for original,replacement in zip('İıŞşĞğÜüÖöÇç','iiSsGgUuOoCc'):
        body=func.replace(body,original,replacement)
    return func.lower(body)


def _like_pattern(term):
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def search_archive(db, context, query, *, after=None, before=None, limit=6, cursor=None,
                         recent=False, episode=False, continuity=False, reference_ids=(), session_ids=()):
    ranked = recent or episode
    if ranked:
        from app.services.memory_query import query_terms
        terms = query_terms(query)[:12]
    else:
        terms = list(dict.fromkeys(fold(query).split()))[:8]
    if not terms and not after and not before and not continuity:
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
    overnight = False
    if continuity:
        local_now = (datetime.fromisoformat(context.prompt_datetime).astimezone(tz)
                     if context.prompt_datetime else datetime.now(tz))
        import re
        overnight = bool(re.search(r"\b(last night|yesterday evening|dun gece|dun aksam)\b", fold(query)))
        # Compare naive UTC instants to the canonical message timestamp column.
        earliest = local_now - timedelta(hours=36)
        if overnight:
            evening = local_now.date() - timedelta(days=1)
            earliest = datetime.combine(evening, time(18), tzinfo=tz)
            morning = datetime.combine(local_now.date(), time(6), tzinfo=tz)
            stmt = stmt.where(Message.created_at < morning.astimezone(timezone.utc).replace(tzinfo=None))
        stmt = stmt.outerjoin(Room, Room.session_id == Session.id).where(
            Room.project_id == context.project_id, Session.id != context.session_id,
            Session.triggered_by == 'user',
        )
        # Keep topic matches across time. Only unanchored continuity is time bounded.
        if overnight:
            stmt = stmt.where(Message.created_at >= earliest.astimezone(timezone.utc).replace(tzinfo=None))
            # The explicit time range already anchors recall. Boilerplate words in the
            # reference must not promote an unrelated evening exchange.
            terms = [term for term in terms if term not in
                     {"last", "night", "yesterday", "evening", "conversation", "talk", "dun", "gece", "aksam"}]
    if context.run_id:
        event=await db.scalar(select(RoomEvent).where(RoomEvent.run_id==context.run_id,
            RoomEvent.room_id==context.room_id,RoomEvent.kind.in_(['user_message','automation_input'])).order_by(RoomEvent.sequence).limit(1))
        if not event: return {'status':'unavailable','detail':'Original run input boundary is unavailable.'}
        stmt=stmt.where(Message.id<event.payload['message_id'])
    elif context.memory_source_id:
        source=await db.get(MemoryChange,context.memory_source_id)
        if source and source.user_id==context.user_id and source.message_id:
            stmt=stmt.where(Message.id<source.message_id)
    if session_ids:
        stmt = stmt.where(Session.id.in_(session_ids))
    if reference_ids:
        stmt = stmt.where(Message.id.in_([span[0] for span in reference_ids[:4]]))
    if cursor is not None: stmt=stmt.where(Message.id<cursor)
    for value,lower in ((after,True),(before,False)):
        if not value: continue
        day=date.fromisoformat(value)
        bound=datetime.combine(day if lower else day+timedelta(days=1),time.min,tzinfo=tz).astimezone(timezone.utc)
        stmt=stmt.where(Message.created_at>=bound) if lower else stmt.where(Message.created_at<bound)
    if after and before and date.fromisoformat(after)>date.fromisoformat(before):
        return {'status':'invalid_date','detail':'The date range is reversed.'}
    if terms and not recent:
        if db.bind.dialect.name == 'postgresql':
            # Scope first so PostgreSQL does not decode large messages for other agents.
            scope = stmt.with_only_columns(Message.id, Message.content).cte('archive_scope').prefix_with('MATERIALIZED')
            body = searchable_body('postgresql', 'archive_scope.content')
            matched = select(scope.c.id).where(body.like(any_(array([_like_pattern(term) for term in terms]))))
            matched = matched.order_by(scope.c.id.desc()).limit(81).subquery()
            stmt = select(Message, Session).join(Session, Message.session_id == Session.id).join(matched, Message.id == matched.c.id)
        else:
            body = searchable_body('sqlite')
            stmt = stmt.where(or_(*(body.contains(term, autoescape=True) for term in terms)))
    candidate_limit = 64 if recent else 80
    candidates = list((await db.execute(stmt.order_by(Message.id.desc()).limit(candidate_limit + 1))).all())
    if not candidates:
        return {'status':'success', 'results':[], 'has_more':False, 'next_before_message_id':None}
    ids = {message.id for message, _ in candidates}
    source_rows = (await db.execute(select(MemoryChange.message_id, MemoryChange.id, MemoryChange.revoked,
        IaconSourceRetirement.source_id).outerjoin(IaconSourceRetirement,
            (IaconSourceRetirement.user_id == MemoryChange.user_id)
            & (IaconSourceRetirement.source_id == MemoryChange.id)).where(
                MemoryChange.user_id == context.user_id, MemoryChange.message_id.in_(ids))
        .order_by(MemoryChange.id))).all()
    source_ids = {}
    withheld = set()
    for message_id, source_id, revoked, retired in source_rows:
        if revoked or retired is not None:
            withheld.add(message_id)
        else:
            source_ids.setdefault(message_id, source_id)
    event_message = RoomEvent.payload['message_id'].as_integer()
    room_ids = select(Room.id).where(Room.user_id == context.user_id,
        Room.session_id.in_({session.id for _, session in candidates}))
    origins = {}
    for message_id, kind in (await db.execute(select(event_message, RoomEvent.kind).where(
        RoomEvent.room_id.in_(room_ids), event_message.in_(ids),
        RoomEvent.kind.in_(['user_message', 'automation_input', 'consult_result'])))).all():
        if kind != 'user_message' or message_id not in origins:
            origins[message_id] = kind
    step_ids = {block['step_id'] for message, _ in candidates if isinstance(message.content, list)
                for block in message.content if isinstance(block, dict) and block.get('type') == '_speaker'
                and isinstance(block.get('step_id'), str)}
    private_steps = set(await db.scalars(select(AgentStep.id).where(
        AgentStep.id.in_(step_ids), AgentStep.mode == 'consult'))) if step_ids else set()
    results=[]
    ranks = {}
    consumed=None
    for message,session in candidates[:candidate_limit]:
        consumed=message.id
        if message.id in withheld or origins.get(message.id) == 'consult_result':
            continue
        blocks = [block for block in message.content if isinstance(block, dict)] if isinstance(message.content, list) else []
        if any(block.get('type') == '_speaker' and block.get('step_id') in private_steps for block in blocks):
            continue
        text=body_text(message.content)
        if not text:
            continue
        if recent:
            text = text[:3200]
        folded=fold(text)
        hits=[folded.find(term) for term in terms if term in folded]
        if terms and not hits and not recent:
            continue
        start=max(0,min(hits)-150) if hits else 0
        length = 900
        if reference_ids:
            _, start, length = next(span for span in reference_ids if span[0] == message.id)
        excerpt=text[start:start+length]
        speaker = next((b.get('agent_id') for b in blocks if b.get('type') == '_speaker'), None)
        automated = origins.get(message.id) == 'automation_input' or any(
            b.get('type') == '_speda_meta' and isinstance(b.get('trigger'), dict)
            and b['trigger'].get('source') == 'n8n' for b in blocks)
        if continuity and speaker and speaker not in aids:
            continue
        if automated:
            attribution = 'automated_input_not_owner_assertion'
            if recent:
                continue
        elif message.role == 'assistant':
            attribution = 'assistant_statement_not_owner_assertion'
        elif origins.get(message.id) == 'user_message' or session.triggered_by == 'user':
            attribution = 'owner_statement'
        else:
            attribution = 'unknown_origin_not_owner_assertion'
        results.append({'message_id':message.id,'reference':f'message:{message.id}','source_id':source_ids.get(message.id),
            'session_id':session.id,'session_title':session.title,'agent_id':speaker or session.agent_id,
            'role':message.role,'attribution':attribution,
            'recorded_at':message.created_at.isoformat(),'text':excerpt,'offset_in_plain_text':start,
            'partial_message':start>0 or start+len(excerpt)<len(body_text(message.content))})
        if ranked:
            ranks[message.id] = sum(min(len(term), 12) for term in terms if term in folded)
        elif len(results)>=min(8,max(1,limit)):
            break
    more = consumed != candidates[-1][0].id
    if ranked:
        if reference_ids:
            visible = {item["message_id"]: item for item in results}
            return {"results": [visible[mid] for mid, _, _ in reference_ids if mid in visible]}
        if continuity:
            episodes = {}
            for item in results:
                episodes.setdefault(item["session_id"], []).append(item)
            from app.services.memory_query import conversation_content
            dialogue = conversation_content(context)
            short_opening = (context.triggered_by == "user" and len(query.split()) <= 4
                             and sum(role == "user" for role, _ in dialogue) <= 1)
            candidates = []
            for turns in episodes.values():
                anchor = max(turns, key=lambda item: (ranks[item["message_id"]], item["message_id"]))
                score = ranks[anchor["message_id"]]
                stamp = datetime.fromisoformat(turns[0]["recorded_at"]).replace(tzinfo=timezone.utc)
                if not score and not overnight and not (short_opening and stamp >= earliest and not session_ids):
                    continue
                # Preserve nearby speaker attribution and the reply or correction around a hit.
                index = turns.index(anchor)
                start = max(0, index - 1)
                candidates.append((score, anchor["message_id"], turns[start:start + limit]))
            candidates.sort(key=lambda item: item[:2], reverse=True)
            selected = list(candidates[0][2]) if candidates else []
            if len(candidates) > 1 and not candidates[0][0] and not overnight:
                # Give an ambiguous reaction two plausible threads instead of asserting one.
                selected = selected[:2] + candidates[1][2][:2]
            selected = selected[:limit]
            return {"status": "partial", "coverage": "recent_episodes", "results": selected}
        if recent and terms:
            related = {}
            for left, right in zip(results, results[1:]):
                if left['session_id'] != right['session_id']:
                    continue
                a, b = left['message_id'], right['message_id']
                related[a] = max(related.get(a, 0), ranks[b] // 2)
                related[b] = max(related.get(b, 0), ranks[a] // 2)
            for identity, score in related.items():
                ranks[identity] += score
            results = [result for result in results if ranks[result['message_id']]>0]
        results.sort(key=lambda result: (ranks[result['message_id']], result['message_id']), reverse=True)
        return {'status':'partial', 'coverage':'recent_bounded' if recent else 'ranked_bounded', 'results':results[:limit],
                'has_more':True, 'next_before_message_id':None}
    return {'status':'partial' if more else 'success','results':results,'has_more':more,
        'next_before_message_id':consumed if more else None,
        'scope':'Literal archive evidence for this agent; bounded results, not an exhaustive semantic search or current personal truth.'}
