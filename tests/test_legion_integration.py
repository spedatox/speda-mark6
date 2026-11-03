import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import json
import time
from uuid import uuid4
import pytest
from sqlalchemy import select,update
from app.models.legion import LegionExecution
from app.models.coordination import Room,RoomEvent,CoordinationRun,ProjectTask,ModelCallReceipt
from app.schemas.llm import LLMMessage,Block,ThinkingRequest,ProviderError
from app.schemas.client_compat import ClientChat
from app.schemas.coordination import CreateRoom,RoomInput
from app.services.client_compat import ClientCompatibility
from app.services.worker_control import WorkerControl
from app.services.coordination_budget import ReplayStream,CoordinationError
from app.skills.legion import LegionInspectSkill,LegionControlSkill
from tests.test_coordination import setup,reply
from tests.test_projects import project_setup
from tests.test_worker_control import environment,parent_context,worker


def assignment(background=False,role='researcher'):
    return LLMMessage([Block(type='tool_use',id='native-worker',name='Task',input={
        'description':'Inspect supplied evidence','prompt':'Find the documented evidence without changing sources',
        'worker_id':role,'background':background})],'tool_use')


async def wire(bits):
    control=WorkerControl(bits.coordinator)
    bits.coordinator.workers=control
    bits.engine._registry._task_runner.activity=control
    await bits.engine._registry.register_skill(LegionInspectSkill())
    await bits.engine._registry.register_skill(LegionControlSkill())
    return control


async def test_foreground_worker_progress_results_and_history_are_separate_from_parent(memory):
    bits=await setup(memory,[assignment(),reply('Cited child finding.'),reply('The parent evaluated the finding.')])
    control=await wire(bits)
    adapter=ClientCompatibility(bits.coordinator)
    admitted=await adapter.start(1,'speda',ClientChat(message='Investigate this.',request_id=str(uuid4())))
    await bits.coordinator.live[admitted['run_id']]
    frames=[json.loads(frame[6:]) async for frame in adapter.stream(1,admitted['request_id']) if frame.startswith('data: ')]
    children=[frame['data'] for frame in frames if frame['type']=='subagent']
    assert children[0]['phase']=='started' and children[-1]['phase']=='finished'
    assert children[-1]['report']=='Cited child finding.'
    assert children[-1]['execution_type']=='legion' and children[-1]['owner_agent']=='speda'
    assert 'Cited child finding.' not in ''.join(frame['data'] for frame in frames if frame['type']=='chunk')
    assert ''.join(frame['data'] for frame in frames if frame['type']=='chunk')=='The parent evaluated the finding.'
    messages=await adapter.messages(1,admitted['session_id'])
    card=next(row for row in messages if row['role']=='assistant')['subagents'][0]
    assert card['report']=='Cited child finding.' and card['executionId']==children[0]['execution_id']
    assert not card['running'] and card['ownerAgent']=='speda'
    assert len(bits.client.calls)==3
    assert bits.client.calls[1]['role']=='worker' and 'anonymous read-only task worker' in bits.client.calls[1]['system']
    await bits.coordinator.shutdown()


async def test_detached_worker_outlives_reply_reconnects_and_keeps_original_budget(memory):
    bits=await setup(memory,[])
    control=await wire(bits)
    release,entered=asyncio.Event(),asyncio.Event()
    root_calls=0
    @asynccontextmanager
    async def stream(**args):
        nonlocal root_calls
        bits.client.calls.append(args)
        if args['role']=='worker':
            entered.set()
            await release.wait()
            result=reply('Detached findings retained.')
        else:
            root_calls+=1
            result=assignment(True) if root_calls==1 else reply('The child is working; this reply can finish.')
        result.policy=args['policy']
        yield ReplayStream({'content':[block.model_dump() for block in result.content],
            'stop_reason':result.stop_reason,'usage':result.usage,'continuation':None},args['policy'])
    bits.client.stream_message=stream
    adapter=ClientCompatibility(bits.coordinator)
    admitted=await adapter.start(1,'speda',ClientChat(message='Run a detached investigation.',request_id=str(uuid4())))
    run_task=bits.coordinator.live[admitted['run_id']]
    frames=await asyncio.wait_for(collect(adapter.stream(1,admitted['request_id'])),10)
    await asyncio.wait_for(entered.wait(),10)
    mapping,_=await adapter.resolve(1,admitted['request_id'])
    assert frames[-1]['data']['status']=='waiting_tasks' and not run_task.done(), await bits.coordinator.events(1,mapping.room_id)
    assert await adapter.active(1,admitted['session_id'])==[]
    active=await control.active(1,admitted['session_id'])
    assert len(active)==1 and active[0]['execution_id']
    execution_id=active[0]['execution_id']
    assert await control.resolve_ticket(1,active[0]['ticket'])==execution_id
    cursor=(await control.events(1,execution_id))['next_cursor']
    release.set()
    await asyncio.wait_for(run_task,10)
    assert (await control.inspect(1,execution_id))['status']=='succeeded'
    tail=[json.loads(frame[6:]) async for frame in control.subscribe(1,execution_id,cursor) if frame.startswith('data: ')]
    assert tail[-1]['data']['report']=='Detached findings retained.'
    run=await bits.coordinator.inspect_run(1,admitted['run_id'])
    assert run['reserved']['calls']==3 and run['status']=='succeeded'
    assert not await control.active(1,admitted['session_id'])
    await bits.coordinator.shutdown()


async def collect(stream):
    return [json.loads(frame[6:]) async for frame in stream if frame.startswith('data: ')]


async def test_native_cwd_binding_is_stable_and_forge_task_stream_is_attributed(memory,tmp_path):
    from forge.runtime import ExecutionResult
    async def executor(spec,**kwargs):
        assert spec.role=='coder'
        return ExecutionResult(spec.job_id,'succeeded','Autobot artifact ready for review')
    bits=await project_setup(memory,tmp_path,[assignment(role='autobot'),reply('The Autobot task is queued.')],executor)
    control=await wire(bits)
    adapter=ClientCompatibility(bits.coordinator)
    admitted=await adapter.start(1,'optimus',ClientChat(message='Implement the change',cwd='/Forge/workspaces/example',request_id=str(uuid4())))
    await bits.coordinator.live[admitted['run_id']]
    mapping,_=await adapter.resolve(1,admitted['request_id'])
    async with memory.sessions() as db:
        room=await db.get(Room,mapping.room_id)
        assert room.project_id==bits.project['id']
    workers=await control.list(1,admitted['session_id'])
    assert len(workers)==1 and workers[0]['team']=='autobots' and workers[0]['owner_agent']=='optimus'
    events=await control.events(1,workers[0]['execution_id'])
    assert events['events'][-1]['report']=='Autobot artifact ready for review'
    assert events['events'][-1]['source']=='forge'
    assert (bits.path/'example.txt').read_text()=='original'
    other=bits.path.parent/'different'
    other.mkdir()
    with pytest.raises(CoordinationError,match='bound to another project'):
        await adapter.start(1,'optimus',ClientChat(message='Do not retarget.',session_id=admitted['session_id'],cwd=str(other),request_id=str(uuid4())))
    await bits.coordinator.shutdown()


async def test_supervisor_collection_is_room_scoped_and_steering_is_not_owner_authority(memory):
    bits,control=await environment(memory)
    bits.coordinator.workers=control
    parent,child,_=await worker(memory,bits,control)
    inspect,command=LegionInspectSkill(),LegionControlSkill()
    result=json.loads(await inspect.execute({'execution_id':child.step_id},parent))
    assert result['owner_agent']=='speda'
    with pytest.raises(ProviderError):await inspect.execute({'execution_id':child.step_id},replace(parent,agent_id='optimus'))
    with pytest.raises(ProviderError):await inspect.execute({'execution_id':child.step_id},replace(parent,room_id=uuid4().hex))
    message_id=uuid4().hex
    args={'execution_id':child.step_id,'action':'message','text':'Focus on the same evidence.','message_id':message_id}
    first=json.loads(await command.execute(args,parent))
    second=json.loads(await command.execute(args,parent))
    assert first['id']==second['id'] and first['status']=='queued'
    async with memory.sessions() as db:
        event=await db.scalar(select(RoomEvent).where(RoomEvent.room_id==parent.room_id,
            RoomEvent.payload['phase'].as_string()=='input_queued'))
        assert event.speaker=='speda' and event.payload['origin']=='agent'
    await bits.coordinator.shutdown()


async def test_child_copies_mcp_incarnation_snapshot_and_archivist_cannot_escape_scope(memory):
    from app.services.task_runner import TaskRunner
    from app.schemas.sse import SSEEvent,SSEEventType
    bits=await setup(memory,[])
    parent=memory.context()
    parent.model='kimi:kimi-k2.6'
    parent.effective_policy=await bits.policy.resolve(model=parent.model)
    parent.extra.update(mcp_grants=frozenset({'fixture'}),mcp_bindings={'fixture':'old-incarnation'},mcp_schemas={'tool':'schema'})
    captured=[]
    class Engine:
        async def run(self,child):
            captured.append(child)
            yield SSEEvent(SSEEventType.CHUNK,'Evidence',child.session_id,child.request_id)
    await TaskRunner(Engine(),bits.policy)({'prompt':'Bounded evidence','worker_id':'archivist'},parent)
    child=captured[0]
    assert child.extra['mcp_bindings']==parent.extra['mcp_bindings'] and child.extra['mcp_bindings'] is not parent.extra['mcp_bindings']
    assert child.extra['mcp_grants']==frozenset() and child.extra['worker_tool_scope']==frozenset({'memory_lookup'})
    assert child.memory_budget is parent.memory_budget
    await bits.coordinator.shutdown()
