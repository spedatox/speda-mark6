"""Regressions from chats 2308, 2309 and 2313; no paid calls or live effects."""
import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from sqlalchemy import select

from app.models.coordination import CoordinationRun, RoomEvent
from app.models.delivery import TelegramOwner, TelegramLink
from app.schemas.coordination import CreateRoom
from app.schemas.llm import Block, LLMMessage, ProviderError
from app.services.damage_control import DamageControl
from app.services.memory_runtime import MemoryRuntime, packet_json
from app.services.remote_tools import request_bytes
from app.skills.memory_lookup import MemoryLookupSkill
from tests.test_coordination import setup, reply, send
from tests.test_operation_waits import start_wait


async def test_critical_tool_finding_is_visible_before_a_later_command_suspends(memory, tmp_path, monkeypatch):
    from app.security_watchdog import Observation
    from app.services.operation_waits import OperationWaits
    from app.skills.system_ops import SystemOpsSkill
    from app.schemas.coordination import RoomInput
    protocol = DamageControl(tmp_path/'incidents.sqlite3')
    protocol.journal.add(Observation('ingress_bps',1794823828,208651006,200000000,10).facts(),status='sent',observed_at=1794823828)
    batch = LLMMessage([
        Block(type='tool_use',id='watchdog',name='system_ops',input={'action':'security_incidents'}),
        Block(type='tool_use',id='host',name='system_ops',input={'action':'exec','command':'check host'}),
    ], 'tool_use')
    bits = await setup(memory,[batch])
    await bits.engine._registry.register_skill(SystemOpsSkill(protocol.journal))
    bits.coordinator.operation_waits = OperationWaits(bits.coordinator)
    async def pending(*args):
        return {'status':'started'}
    monkeypatch.setattr('app.services.host_operations.host_operation',pending)
    room = await bits.coordinator.create(1,CreateRoom(lead='orion',members=['orion']))
    try:
        run = await bits.coordinator.start(1,room['id'],RoomInput(input_id='investigate',message='Check the live incident'))
        await bits.coordinator.live[run['run_id']]
        async with memory.sessions() as db:
            events = list(await db.scalars(select(RoomEvent).where(RoomEvent.run_id==run['run_id']).order_by(RoomEvent.sequence)))
        finding = next(e for e in events if e.kind=='progress')
        waiting = next(e for e in events if e.kind=='step_waiting')
        assert '208,651,006' in finding.payload['text'] and '13:10' in finding.payload['text']
        assert finding.sequence < waiting.sequence
        assert '208,651,006' in waiting.payload['status']
        assert len(bits.client.calls) == 1
    finally:
        await bits.coordinator.shutdown()


async def test_ultron_gets_saved_courses_before_answering_without_a_model_lookup(memory):
    from app.services.iacon_catalogue import Catalogue
    from app.skills.course_memory import ReadCourseMemorySkill
    from tests.test_course_memory import save_course
    catalogue = Catalogue(memory.store)
    await save_course(memory, catalogue, '2026-2027-fall', '# MIS231 — Data Structures', code='MIS231')
    bits = await setup(memory, [reply('I will reconcile the sheet against your saved courses.')])
    await bits.engine._registry.register_skill(ReadCourseMemorySkill(catalogue))
    room = await bits.coordinator.create(1, CreateRoom(lead='ultron', members=['ultron']))
    try:
        assert (await send(bits, room, 'Check my exam schedule'))['status'] == 'succeeded'
        assert '2026-2027-fall/MIS231.md' in bits.client.calls[0]['system']
        assert '<domain_context>' in bits.client.calls[0]['system']
        other = memory.context(agent_id='speda', allowed_tools=bits.profiles.require('speda').tools)
        assert await bits.engine._registry.context_data(other) == {}
    finally:
        await bits.coordinator.shutdown()


async def test_exact_memory_evidence_survives_peer_and_return_turns(memory):
    statement = 'From now on, record this as my stated relationship boundary.'
    await memory.seed(statement, role='owner', publish=False)
    lookup = LLMMessage([Block(type='tool_use', id='lookup', name='memory_lookup', input={'query':'relationship boundary'})], 'tool_use')
    delegate = LLMMessage([Block(type='tool_use', id='delegate', name='collaborate',
        input={'action':'address','agent_id':'orion','message':'Prepare an attributed proposal using the retrieved evidence.'})], 'tool_use')
    bits = await setup(memory, [lookup, delegate, reply('Please prepare the proposal.'), reply('Proposal prepared.'), reply('The proposal is ready.')])
    await bits.engine._registry.register_skill(MemoryLookupSkill(MemoryRuntime(memory.store)))
    room = await bits.coordinator.create(1, CreateRoom(members=['speda','orion']))
    try:
        run = await send(bits, room, 'Update the relationship boundary record')
        assert run['status'] == 'succeeded'
        for call in bits.client.calls[-2:]:
            assert statement in call['system']
            assert '<shared_retrieval_evidence>' in call['system']
            assert 'source:' in call['system']
        async with memory.sessions() as db:
            saved = await db.get(CoordinationRun, run['id'])
            assert saved.memory_budget['lookup_results'][0]['result']['evidence']
    finally:
        await bits.coordinator.shutdown()


async def test_native_person_dossier_outranks_empty_entity_and_shared_surname(memory):
    empty = json.dumps({'record_id':'a'*32,'version':1,'kind':'entity','title':'Mira Stone',
        'payload':{'entity_type':'person','description':''}})
    entity_source, _ = await memory.seed(empty, title='Mira Stone', aliases=('Mira',), key='iacon:'+'a'*32)
    dossier_source, _ = await memory.seed("**Who:** Mira is the owner's sister.\n\nHer dated record includes a later change in the owner's stance.",
        title='/memories/social/personal/mira-stone.md', aliases=('Mira Stone','Mira'), key='record:dossier')
    from app.models.memory_serving import MemoryChange
    async with memory.sessions() as db, db.begin():
        for source_id in (entity_source, dossier_source):
            source = await db.get(MemoryChange, source_id)
            source.provenance = {'entity_id':'a'*32}
    for name in ['Osman', 'Semra', 'Erol']:
        await memory.seed(name + ' has separate family events.', title=name+' Stone', aliases=(name,))
    # A related person's snapshot can match lexical search by mentioning Mira.
    # Sharing her surname must not turn it into a full-name identity match.
    await memory.seed(json.dumps({'record_id':'b'*32,'version':1,'kind':'entity',
        'payload':{'entity_type':'person','description':"Mira's relative"}}),
        title='Other Stone', aliases=('Other',), key='iacon:'+'b'*32)
    packet = json.loads(packet_json(await MemoryRuntime(memory.store).prepare(memory.context('Update Mira Stone records'))))
    assert "owner's sister" in packet['evidence'][0]['text']
    assert packet['catalogue_records'][0]['record_id'] == 'a'*32
    assert packet['catalogue_records'][0]['version'] == 1
    assert not any('separate family events' in item['text'] for item in packet['evidence'])
    assert len(packet['catalogue_records']) == 1
    assert not packet['ambiguities']
    surname = json.loads(packet_json(await MemoryRuntime(memory.store).prepare(memory.context('Who is Stone?'))))
    assert surname['ambiguities']


async def test_missing_completion_socket_recovers_from_saved_receipt_without_redispatch(memory, monkeypatch):
    bits, waits, run_id, _, submissions, _ = await start_wait(memory, monkeypatch)
    checks = []
    async def inspect(args, context):
        checks.append(args)
        return {'status':'ok', 'stdout':'saved command output', 'exit_code':0}
    monkeypatch.setattr('app.services.host_operations.host_operation', inspect)
    try:
        await waits.reconcile_waiting()
        await waits.drain()
        await bits.coordinator.live[run_id]
        assert (await bits.coordinator.inspect_run(1, run_id))['status'] == 'succeeded'
        assert len(submissions) == 1
        assert checks[0]['action'] == 'operation_status'
        assert 'saved command output' in json.dumps(bits.client.calls[-1]['messages'])
    finally:
        await bits.coordinator.shutdown()


async def test_pending_command_reports_delay_once_without_model_polling(memory, monkeypatch):
    bits, waits, run_id, _, _, _ = await start_wait(memory, monkeypatch)
    async with memory.sessions() as db, db.begin():
        run = await db.get(CoordinationRun, run_id)
        run.created_at = datetime.utcnow() - timedelta(seconds=40)
    try:
        await waits.reconcile_waiting()
        waits.last_reconcile = 0
        await waits.reconcile_waiting()
        async with memory.sessions() as db:
            notices = list(await db.scalars(select(RoomEvent).where(RoomEvent.run_id==run_id, RoomEvent.kind=='progress')))
        assert len(notices) == 1
        assert 'saved receipt' in notices[0].payload['text']
        assert len(bits.client.calls) == 1
    finally:
        await bits.coordinator.shutdown()


async def test_connect_failure_is_distinct_from_uncertain_post(monkeypatch):
    original = httpx.AsyncClient
    def handler(request):
        raise httpx.ConnectTimeout('fixture connect timeout')
    monkeypatch.setattr('app.services.remote_tools.httpx.AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    try:
        await request_bytes('POST', 'https://fixture.test')
    except ProviderError as exc:
        assert exc.code == 'remote_not_dispatched'
    else:
        raise AssertionError('Connection failure must be classified')


async def test_security_alert_precedes_slow_assessment_and_retries_unsent_warning(tmp_path, memory):
    protocol = DamageControl(tmp_path/'incidents.sqlite3')
    async with memory.sessions() as db, db.begin():
        db.add(TelegramOwner(user_id=1,sender_id=99))
        db.add(TelegramLink(user_id=1,agent_id='speda',chat_id=99))
    release = asyncio.Event()
    async def analyse(facts):
        await release.wait()
        return 'Protection checked and verified.'
    telegram = AsyncMock(side_effect=ProviderError('remote_not_dispatched','Connection was not established'))
    deliveries = SimpleNamespace(sessions=memory.sessions,config=SimpleNamespace(telegram_owner_id=99),
        token=lambda _: 'fixture',telegram=telegram)
    identity = protocol.journal.add({'component':'security_watchdog','event':'security_ingress_bps','summary':'High traffic'})
    task = asyncio.create_task(protocol.process(protocol.journal.next(),analyse,deliveries))
    try:
        async with asyncio.timeout(3):
            while not telegram.await_count:
                await asyncio.sleep(.01)
        assert not release.is_set()
        with protocol.journal.connect() as db:
            state = db.execute('SELECT status,next_attempt FROM incidents WHERE id=?',(identity,)).fetchone()
        assert state['status'] == 'ready' and state['next_attempt'] > 0
        release.set()
        await task
    finally:
        task.cancel()
        await asyncio.gather(task,return_exceptions=True)
