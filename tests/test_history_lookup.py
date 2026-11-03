import json
from datetime import datetime
from sqlalchemy import select
from app.models.session import Session
from app.models.message import Message
from app.models.client_compat import HiddenMessage,ClientSession
from app.services.memory_store import capture_source
from app.services.memory_runtime import MemoryRuntime
from app.skills.memory_lookup import MemoryLookupSkill
from app.skills.search_history import SearchHistorySkill


async def seed(memory, body, *, owner=1, agent='speda',role='user',stamp=None):
    async with memory.sessions() as db,db.begin():
        session=Session(user_id=owner,agent_id=agent,triggered_by='user',model_used='fixture',title='Archive')
        db.add(session)
        await db.flush()
        message=Message(session_id=session.id,role=role,content=body,
            **({'created_at':stamp} if stamp else {}))
        db.add(message)
        await db.flush()
        source=await capture_source(db,user_id=owner,source_key=f'message:{message.id}',
            content=body if isinstance(body,str) else 'original statement',source_role='owner' if role=='user' else 'quoted',
            session_id=session.id,message_id=message.id)
        return session.id,message.id,source.id


async def test_archive_uses_real_text_owner_agent_scope_and_one_shared_batch(memory):
    _,identity,source=await seed(memory,[{'type':'text','text':'İLAYDA ile bütçeyi konuştuk.'}])
    await seed(memory,'İlayda confidential',owner=2)
    await seed(memory,'İlayda private Sentinel chat',agent='sentinel')
    await seed(memory,[{'type':'_provider_state','text':'İlayda metadata only'}])
    lookup=MemoryLookupSkill(MemoryRuntime(memory.store))
    context=memory.context()
    result=json.loads(await SearchHistorySkill(lookup).execute({'query':'ilayda'},context))
    assert [r['message_id'] for r in result['results']]==[identity]
    assert result['results'][0]['source_id']==source
    assert context.memory_budget.lookup_batches==1 and not lookup.available(context)
    assert 'budget exhausted' in await lookup.execute({'query':'İlayda'},context)


async def test_archive_date_range_is_owner_local_and_hidden_originals_remain_retained(memory):
    session,identity,_=await seed(memory,'Original midnight wording',stamp=datetime(2026,10,24,22,10))
    _,outside,_=await seed(memory,'Outside the local date',stamp=datetime(2026,10,24,19))
    lookup=MemoryLookupSkill(MemoryRuntime(memory.store))
    result=json.loads(await lookup.execute({'query':'','corpus':'conversation_history','after':'2026-10-25','before':'2026-10-25'},memory.context()))
    assert [r['message_id'] for r in result['results']]==[identity]
    async with memory.sessions() as db,db.begin():
        db.add(HiddenMessage(session_id=session,message_id=identity,request_id='display-edit'))
    result=json.loads(await lookup.execute({'query':'Original','corpus':'conversation_history'},memory.context()))
    assert result['results']==[]
    async with memory.sessions() as db: assert await db.get(Message,identity)


async def test_archive_packing_keeps_cursors_and_assistant_attribution_honest(memory):
    expected=[]
    for index in range(9):
        _,identity,_=await seed(memory,[{'type':'text','text':f'Ledger evidence {index} '+('a'*900)},
            {'type':'_speaker','agent_id':'sentinel'}],role='assistant')
        expected.append(identity)
    lookup=MemoryLookupSkill(MemoryRuntime(memory.store))
    context=memory.context()
    first=json.loads(await lookup.execute({'query':'Ledger','corpus':'conversation_history','limit':8},context))
    assert first['has_more'] and context.memory_budget.used<=context.memory_budget.lookup_limit
    assert all(r['attribution']=='assistant_statement_not_owner_assertion' and r['agent_id']=='sentinel' for r in first['results'])
    second=json.loads(await lookup.execute({'query':'Ledger','corpus':'conversation_history',
        'before_message_id':first['next_before_message_id']},memory.context()))
    assert not ({r['message_id'] for r in first['results']} & {r['message_id'] for r in second['results']})
    assert second['results'][0]['message_id']==max(set(expected)-{r['message_id'] for r in first['results']})
