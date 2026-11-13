"""Exercise real registry/coordinator boundaries without a provider or host send."""
import asyncio
import json
import pytest
from sqlalchemy import select
from app.models.operation_wait import OperationBinding, ExecutorInbox, OperationContinuation
from app.models.coordination import Room
from app.schemas.coordination import CreateRoom, RoomInput
from app.schemas.llm import Block, LLMMessage
from app.services.operation_waits import OperationWaits
from app.services.coordination_budget import digest
from app.skills.system_ops import SystemOpsSkill
from tests.test_coordination import setup, reply


async def start_wait(memory, monkeypatch, immediate=False, batch=False):
    request = LLMMessage([Block(type='tool_use', id='command-one', name='system_ops',
        input={'action':'exec', 'command':'echo done'})], 'tool_use',
        continuation={'type':'_provider_state', 'origin':'kimi:kimi-k2.6', 'protocol':'chat', 'payload':{}})
    if batch:
        request.content.append(Block(type="tool_use", id="command-two", name="system_ops", input={"action":"exec", "command":"echo second"}))
    bits = await setup(memory, [request, reply('The command finished.')])
    await bits.engine._registry.register_skill(SystemOpsSkill(None))
    waits = bits.coordinator.operation_waits = OperationWaits(bits.coordinator)
    submissions = []
    async def host(args, context):
        submissions.append(args)
        return {'status':'ok', 'stdout':'done', 'exit_code':0} if immediate else {'status':'started'}
    monkeypatch.setattr('app.services.host_operations.host_operation', host)
    room = await bits.coordinator.create(1, CreateRoom(lead='orion', members=['orion']))
    started = await bits.coordinator.start(1, room['id'], RoomInput(input_id='command', message='Run echo done'))
    await bits.coordinator.live[started['run_id']]
    await asyncio.sleep(0)
    async with memory.sessions() as db:
        binding = (await db.scalars(select(OperationBinding))).one()
    result = {'status':'ok', 'stdout':'done', 'exit_code':0}
    event = dict(schema_version=1, executor=waits.executor, event_id='event-one', executor_instance='host',
        sequence=1, operation_id=binding.operation_id, fingerprint=binding.fingerprint,
        state_version=1, result=result, result_hash=digest(result))
    return bits, waits, started['run_id'], room, submissions, event


async def test_suspends_without_paid_polling_and_resumes_with_original_pair(memory, monkeypatch):
    bits, waits, run_id, room, submissions, event = await start_wait(memory, monkeypatch)
    state = await bits.coordinator.inspect_run(1, run_id)
    assert state['status'] == 'waiting_operations'
    assert len(bits.client.calls) == len(submissions) == 1
    async with memory.sessions() as db:
        assert (await db.get(Room, room['id'])).active_run is None
    await waits.accept(event)
    await waits.accept(event)
    await waits.drain()
    await bits.coordinator.live[run_id]
    assert len(bits.client.calls) == 2 and len(submissions) == 1
    assert (await bits.coordinator.inspect_run(1, run_id))['status'] == 'succeeded'
    messages = bits.client.calls[1]['messages']
    pairs = [b for m in messages if isinstance(m['content'], list) for b in m['content'] if b['type'] == 'tool_result']
    assert len(pairs) == 1 and pairs[0]['tool_use_id'] == 'command-one'
    assert json.loads(pairs[0]['content'])['stdout'] == 'done'
    async with memory.sessions() as db:
        assert len(list(await db.scalars(select(ExecutorInbox)))) == 1
        assert (await db.scalars(select(OperationContinuation))).one().status == 'consumed'


async def test_immediate_completion_and_event_attach_only_once(memory, monkeypatch):
    bits, waits, run_id, _, submissions, event = await start_wait(memory, monkeypatch, immediate=True)
    await waits.accept(event)
    await waits.drain()
    assert (await bits.coordinator.inspect_run(1, run_id))['status'] == 'succeeded'
    assert len(bits.client.calls) == 2 and len(submissions) == 1


@pytest.mark.parametrize('stop', ['run', 'reply'])
async def test_completion_never_resumes_stopped_step(memory, monkeypatch, stop):
    bits, waits, run_id, _, _, event = await start_wait(memory, monkeypatch)
    if stop == 'run':
        await bits.coordinator.cancel(1, run_id)
    else:
        state = await bits.coordinator.inspect_run(1, run_id)
        assert (await bits.coordinator.stop_reply(1, state['steps'][0]['id']))['accepted']
    await waits.accept(event)
    await waits.drain()
    assert len(bits.client.calls) == 1


async def test_bad_hash_and_sequence_gap_do_not_advance_cursor(memory, monkeypatch):
    _, waits, _, _, _, event = await start_wait(memory, monkeypatch)
    with pytest.raises(ValueError):
        await waits.accept({**event, 'result_hash':'forged'})
    with pytest.raises(ValueError):
        await waits.accept({**event, 'sequence':2})
    assert await waits.accept(event) == 1


async def test_unknown_execution_result_stays_held(memory, monkeypatch):
    bits, waits, run_id, _, _, event = await start_wait(memory, monkeypatch)
    result = {'status':'unknown', 'reason':'Worker lost execution evidence'}
    await waits.accept({**event, 'result':result, 'result_hash':digest(result)})
    await waits.drain()
    assert len(bits.client.calls) == 1
    state = await bits.coordinator.inspect_run(1, run_id)
    assert state['operations'][0]['state'] == 'reconciling'


async def test_batch_cursor_preserves_prior_results_and_never_reexecutes(memory, monkeypatch):
    bits, waits, run_id, _, submissions, event = await start_wait(memory, monkeypatch, batch=True)
    await waits.accept(event)
    await waits.drain()
    await bits.coordinator.live[run_id]
    await asyncio.sleep(0)
    assert len(bits.client.calls) == 1 and len(submissions) == 2
    async with memory.sessions() as db:
        second = (await db.scalars(select(OperationBinding).where(OperationBinding.operation_id != event['operation_id']))).one()
    await waits.accept({**event, 'event_id':'event-two', 'sequence':2,
        'operation_id':second.operation_id, 'fingerprint':second.fingerprint})
    await waits.drain()
    await bits.coordinator.live[run_id]
    assert len(bits.client.calls) == 2 and len(submissions) == 2
    pairs = [b for m in bits.client.calls[-1]['messages'] if isinstance(m['content'], list) for b in m['content'] if b['type'] == 'tool_result']
    assert [p['tool_use_id'] for p in pairs] == ['command-one', 'command-two']


async def test_new_executor_can_resume_saved_wait(memory, monkeypatch):
    bits, waits, run_id, _, submissions, event = await start_wait(memory, monkeypatch)
    from app.services.coordination import Coordinator
    c = Coordinator(memory.sessions, bits.profiles, bits.engine, bits.policy, bits.budget)
    c.operation_waits = replacement = OperationWaits(c)
    await replacement.recover()
    await c.recover()
    await replacement.accept(event)
    await replacement.drain()
    await c.live[run_id]
    assert len(bits.client.calls) == 2 and len(submissions) == 1


async def test_paused_budget_holds_completion_until_unpaused(memory, monkeypatch):
    bits, waits, run_id, _, _, event = await start_wait(memory, monkeypatch)
    version, policy = await bits.budget.policy(1)
    await bits.budget.save(1, version, policy.model_copy(update={'paused':True}))
    await waits.accept(event)
    await waits.drain()
    assert len(bits.client.calls) == 1
    version, policy = await bits.budget.policy(1)
    await bits.budget.save(1, version, policy.model_copy(update={'paused':False}))
    await waits.drain()
    await bits.coordinator.live[run_id]
    assert len(bits.client.calls) == 2


async def test_conflicting_duplicate_holds_original_continuation(memory, monkeypatch):
    bits, waits, _, _, _, event = await start_wait(memory, monkeypatch)
    await waits.accept(event)
    result = {'status':'ok', 'stdout':'different output'}
    await waits.accept({**event, 'result':result, 'result_hash':digest(result)})
    await waits.drain()
    assert len(bits.client.calls) == 1
    async with memory.sessions() as db:
        assert (await db.scalars(select(ExecutorInbox))).one().disposition == 'conflict'
