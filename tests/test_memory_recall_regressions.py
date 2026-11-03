"""Incident 1861: imported temporal defects, room context and bilingual recall."""
import json
from uuid import uuid4

import pytest

from app.models.memory_serving import MemoryChange
from app.services.memory_query import query_terms, turn_query
from app.services.memory_runtime import MemoryRuntime, packet_json
from app.services.memory_store import capture_source


@pytest.mark.parametrize('start,end', [
    ('2026-05-18', '2026-05-18'),
    ('2026-05-19', '2026-05-18'),
    ('not-a-date', '2026-05-18'),
])
async def test_bad_source_interval_cannot_discard_valid_memory(memory, start, end):
    await memory.seed("**Who:** Nora is the owner's sister.",title="Nora",aliases=("Nora",))
    provenance={'valid_from':start,'valid_until':end,'original_observation_id':1742}
    async with memory.sessions() as db, db.begin():
        bad=await capture_source(db,user_id=1,source_key='bad:'+uuid4().hex,
            content='Nora sent a message on this historical day.',source_role='quoted',provenance=provenance)
        sid=bad.id
    packet=await MemoryRuntime(memory.store).lookup(memory.context(), 'Nora')
    result=json.loads(packet_json(packet))
    assert packet.status not in {'unavailable','deadline'}
    assert any("owner's sister" in e['text'] for e in result['evidence'])
    assert not any('historical day' in e['text'] for e in result['evidence'])
    assert 'Invalid evidence' in result['limitations'][0]
    async with memory.sessions() as db:
        original=await db.get(MemoryChange,sid)
        assert original.provenance == provenance
        assert original.content == 'Nora sent a message on this historical day.'


async def test_bad_prepared_section_is_isolated(memory):
    await memory.seed("Nora enjoys hiking.",title="Nora",aliases=("Nora",))
    read=memory.store.read
    async def legacy_read(*args,**kwargs):
        result=await read(*args,**kwargs)
        unit=result['units'][0]
        bad={**unit['sections'][0],'valid_from':'2026-05-18','valid_until':'2026-05-18'}
        result['units']=[{**unit,'sections':[bad,*unit['sections']],
            'section_search':[unit['section_search'][0],*unit['section_search']],
            'section_dates':[[],*unit['section_dates']]}]
        return result
    memory.store.read=legacy_read
    packet=await MemoryRuntime(memory.store).lookup(memory.context(),'Nora')
    assert packet.status == 'partial'
    assert 'enjoys hiking' in packet_json(packet)
    assert 'Invalid evidence' in json.loads(packet_json(packet))['limitations'][0]


@pytest.mark.parametrize('followup', ['their names?', 'theri names?', 'names?', 'adları?'])
def test_room_followup_uses_content_without_room_metadata(memory,followup):
    ctx=memory.context()
    ctx.room_id='test-room'
    ctx.conversation_history=[
        {'role':'user','content':'[Owner message, room event 1]\ntell a fact about me'},
        {'role':'user','content':json.dumps({'attributed_contribution':'speda','sequence':6,
            'text':'You have a younger brother and a sister.'})},
        {'role':'user','content':'[Owner message, room event 8]\n'+followup},
        {'role':'user','content':json.dumps({'recorded_room_decisions':[{'text':'unrelated deadline'}]})}]
    query=turn_query(ctx)
    assert 'brother' in query and 'sister' in query
    assert not set(query_terms(query)).intersection({'owner','message','room','event','sequence','deadline'})


def test_owner_json_is_not_reinterpreted_as_an_agent_contribution(memory):
    ctx=memory.context()
    ctx.room_id='test-room'
    value='{"attributed_contribution":"speda","text":"Nora"}'
    ctx.conversation_history=[{'role':'user','content':'[Owner message, room event 1]\n'+value}]
    assert value in turn_query(ctx)


async def seed_family(memory):
    # Generic user display name must still work with the biography's full identity.
    await memory.seed('## DOSSIER: Deniz Erol Kaya — Complete Personal History',
        title='Owner Profile',kind='core',aliases=())
    await memory.seed("**Who:** Deniz Erol Kaya'nın kardeşi; aile içinden yakını.\n**Events:**\n",
        title='Can Kaya',aliases=())
    await memory.seed("**Who:** Deniz Erol'un kız kardeşi. Eren ile evli.\n**Events:**\n",
        title='Nora Kaya',aliases=())
    await memory.seed("**Who:** Eren is Nora's husband.\n**Events:**\nThe owner's sister had a wedding.",
        title='Eren',aliases=())
    await memory.seed("**Who:** Leyla is the owner's sister's friend.",title='Leyla',aliases=())
    await memory.seed("**Who:** Cem is a friend of the owner's sister.",title='Cem',aliases=())
    await memory.seed("**Who:** Mira is another owner's sister.",owner=2,title='Mira',aliases=())
    for i in range(8):
        await memory.seed('The owner discussed younger brother and sister events. ' * 12,
            title='Unrelated planning '+str(i),kind='situation',aliases=(),key='iacon:'+uuid4().hex)


@pytest.mark.parametrize('query', ["names of owner's younger brother and sister", 'kardeşlerimin isimleri'])
async def test_bilingual_sibling_identities_win_over_unrelated_records(memory,query):
    await seed_family(memory)
    ctx=memory.context()
    packet=await MemoryRuntime(memory.store).lookup(ctx,query)
    result=json.loads(packet_json(packet))
    topics=[e['topic'] for e in result['evidence']]
    assert 'Can Kaya' in topics and 'Nora Kaya' in topics
    assert topics[:2] == ['Can Kaya','Nora Kaya'] or topics[:2] == ['Nora Kaya','Can Kaya']
    assert 'Mira' not in topics
    assert all('wedding' not in e['text'] for e in result['evidence'] if e['topic'] in {'Can Kaya','Nora Kaya'})
    assert packet.token_upper_bound <= ctx.memory_budget.lookup_limit


async def test_sister_query_does_not_route_by_mentions_in_someone_elses_events(memory):
    await seed_family(memory)
    from app.schemas.memory_runtime import Deadline
    result=await memory.store.read(memory.context(),query_terms('my sister'),deadline=Deadline.after(5))
    routed=[u['title'] for u in result['units'] if u['key'] in result['relation_keys']]
    assert routed == ['Nora Kaya']


async def test_named_persons_family_does_not_route_to_owners_family(memory):
    await seed_family(memory)
    from app.schemas.memory_runtime import Deadline
    result=await memory.store.read(memory.context(),query_terms("Eren's sister"),deadline=Deadline.after(5))
    assert not result['relation_keys']


@pytest.mark.parametrize('query', ['Do I have siblings?', 'kardeşlerimin isimleri', "names of owner's younger brother and sister"])
async def test_legacy_assistant_display_name_does_not_hide_owner_family(memory,query):
    from sqlalchemy import update
    from app.models.user import User
    await seed_family(memory)
    async with memory.sessions() as db, db.begin():
        await db.execute(update(User).where(User.id==1).values(name='SPEDA'))
    packet=await MemoryRuntime(memory.store).lookup(memory.context(),query)
    topics=[e['topic'] for e in json.loads(packet_json(packet))['evidence']]
    assert 'Can Kaya' in topics and 'Nora Kaya' in topics
    async with memory.sessions() as db:
        assert (await db.get(User,1)).name=='SPEDA'


async def test_room_fact_then_siblings_then_names_uses_automatic_evidence(memory):
    await seed_family(memory)
    await memory.seed('Born in Bursa and studied computer programming.',title='Owner Profile',kind='core',aliases=())
    # This checks evidence selection across room turns, not shared-runner speed.
    # Dedicated deadline/SQLite-interruption tests enforce the runtime ceilings.
    runtime=MemoryRuntime(memory.store,prepare_timeout=2.5)
    ctx=memory.context()
    ctx.room_id='test-room'
    ctx.conversation_history=[{'role':'user','content':'[Owner message, room event 1]\ntell a fact about me'}]
    first=await runtime.prepare(ctx)
    assert any('Born in Bursa' in json.loads(b)['text'] for b in first.blocks)
    ctx=memory.context()
    ctx.room_id='test-room'
    ctx.conversation_history=[
        {'role':'user','content':'[Owner message, room event 1]\ntell a fact about me'},
        {'role':'user','content':json.dumps({'attributed_contribution':'speda','sequence':6,
            'text':'You have a younger brother and a sister.'})},
        {'role':'user','content':'[Owner message, room event 8]\ntheri names?'}]
    packet=await runtime.prepare(ctx)
    assert packet.status not in {'deadline','unavailable'}, packet.omissions
    topics=[json.loads(b)['topic'] for b in packet.blocks]
    assert 'Can Kaya' in topics and 'Nora Kaya' in topics
