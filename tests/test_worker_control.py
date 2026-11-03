"""Durable worker presentation/control contracts; no real provider or Cell."""
import asyncio
from dataclasses import replace
import json
import time
from types import SimpleNamespace
from uuid import uuid4
import pytest
from sqlalchemy import select, update, func
from app.models.coordination import (Room, CoordinationRun, AgentStep, Project, ProjectTask,
    RoomEvent, TaskInput, ModelCallReceipt)
from app.models.legion import LegionExecution, WorkerTicket, LegionInput, WorkerEventArtifact
from app.models.model_policy import ToolExecutionReceipt
from app.schemas.coordination import CreateRoom, RoomInput
from app.schemas.llm import PolicyDocument
from app.services.coordination_budget import CoordinationError
from app.services.worker_control import WorkerControl, LegionInbox
from tests.test_coordination import setup


async def environment(memory):
    bits = await setup(memory, [])
    bits.coordinator.launch = lambda _: None
    control = WorkerControl(bits.coordinator)
    return bits, control


async def parent_context(memory, bits, owner=1):
    if owner != 1:
        await bits.policy.save(owner, 0, PolicyDocument(main_model='kimi:kimi-k2.6'))
        await bits.budget.save(owner, 0, bits.spending)
    room = await bits.coordinator.create(owner, CreateRoom())
    admitted = await bits.coordinator.start(owner, room['id'], RoomInput(input_id=uuid4().hex, message='Review the supplied evidence'))
    async with memory.sessions() as db, db.begin():
        await db.execute(update(CoordinationRun).where(CoordinationRun.id == admitted['run_id'])
            .values(status='running', executor=bits.coordinator.identity, lease_until=time.time()+300))
        step = await db.scalar(select(AgentStep).where(AgentStep.run_id == admitted['run_id']))
        step_id = step.id
        step.status='running'
        stored_room = await db.get(Room,room['id'])
        session_id = stored_room.session_id
    return replace(memory.context(user_id=owner), session_id=session_id, room_id=room['id'],
        run_id=admitted['run_id'], step_id=step_id, agent_id='speda',
        coordination=bits.coordinator, effective_policy=await bits.policy.resolve(user_id=owner), budget_handle=bits.budget)


async def worker(memory, bits, control, *, parent=None, owner=1, role='researcher'):
    parent = parent or await parent_context(memory,bits,owner)
    execution_id = uuid4().hex
    admitted = await control.begin(parent,execution_id,role,'Find evidence, preserve attribution','kimi:kimi-k2.6',True)
    context = replace(parent,step_id=execution_id,request_id=execution_id,parent_step_id=parent.step_id,task_role='worker')
    await control.start(context)
    return parent,context,admitted


async def test_worker_owner_isolation_stable_tickets_and_duplicate_admission(memory):
    bits,control = await environment(memory)
    parent,context,admitted = await worker(memory,bits,control)
    first = await control.inspect(1,context.step_id)
    second = await control.inspect(1,context.step_id)
    assert first['ticket_id'] == second['ticket_id'] == admitted['ticket']
    assert await control.resolve_ticket(1,first['ticket_id']) == context.step_id
    duplicate = await control.begin(parent,context.step_id,'researcher','Find evidence, preserve attribution','kimi:kimi-k2.6',True)
    assert duplicate['duplicate']
    assert len(await control.list(1,parent.session_id)) == 1
    assert await control.list(2) == []
    for operation in (control.inspect(2,context.step_id),control.events(2,context.step_id),
                      control.resolve_ticket(2,first['ticket_id']),control.interrupt(2,context.step_id),
                      control.message(2,context.step_id,'Not authorized',uuid4().hex)):
        with pytest.raises(CoordinationError): await operation
    with pytest.raises(CoordinationError,match='another task'):
        await control.begin(parent,context.step_id,'researcher','Different task','kimi:kimi-k2.6',True)
    async with memory.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(WorkerTicket)) == 1
        assert await db.scalar(select(func.count()).select_from(LegionExecution)) == 1
    assert not bits.client.calls
    await bits.coordinator.shutdown()


async def test_worker_event_cursor_replay_and_unknown_executor_are_honest(memory):
    bits,control = await environment(memory)
    _,context,_ = await worker(memory,bits,control)
    await control.publish(context,{'phase':'text','text':'First finding'})
    initial = await control.events(1,context.step_id)
    assert initial['execution']['status'] == 'unknown'
    assert not initial['execution']['executor_available']
    assert initial['events'][-1]['text'] == 'First finding'
    assert len({event['event_id'] for event in initial['events']}) == len(initial['events'])
    cursor = initial['next_cursor']
    await control.publish(context,{'phase':'text','text':'Second finding'})
    tail = await control.events(1,context.step_id,cursor)
    assert [event['text'] for event in tail['events']] == ['Second finding']
    assert (await control.events(1,context.step_id,tail['next_cursor']))['events'] == []
    frames = [json.loads(frame.removeprefix('data: ')) async for frame in control.subscribe(1,context.step_id,cursor) if frame.startswith('data: ')]
    assert frames[0]['data']['text'] == 'Second finding'
    assert frames[-1]['data']['phase'] == 'executor_unavailable'
    assert frames[-1]['data']['status'] == 'unknown'
    assert all(frame['data'].get('status') != 'succeeded' for frame in frames)
    with pytest.raises(CoordinationError,match='cursor'): await control.events(1,context.step_id,-1)
    with pytest.raises(CoordinationError,match='cursor'): await control.events(1,context.step_id,tail['last_sequence']+1)
    await control.finish(context,'succeeded','Verified findings')
    terminal = [json.loads(frame.removeprefix('data: ')) async for frame in control.subscribe(1,context.step_id,tail['next_cursor']) if frame.startswith('data: ')]
    assert terminal[-1]['data']['phase'] == 'finished'
    assert terminal[-1]['data']['status'] == 'succeeded'
    assert not bits.client.calls
    await bits.coordinator.shutdown()


async def test_worker_steering_is_idempotent_and_boundary_delivery_is_not_compliance(memory):
    bits,control = await environment(memory)
    _,context,_ = await worker(memory,bits,control)
    message_id = uuid4().hex
    accepted = await control.message(1,context.step_id,'Prioritize the source dates.',message_id)
    retry = await control.message(1,context.step_id,'Prioritize the source dates.',message_id)
    assert accepted['id'] == retry['id'] and retry['status'] == 'queued'
    assert 'not yet included' in accepted['meaning']
    with pytest.raises(CoordinationError,match='another instruction'):
        await control.message(1,context.step_id,'Different content.',message_id)
    inbox = LegionInbox(control,context)
    assert await inbox.claim() == ['Prioritize the source dates.']
    delivering = await control.inspect(1,context.step_id)
    assert delivering['messages'][0]['status'] == 'delivering'
    assert delivering['messages'][0]['boundary_hash'] is None
    await inbox.record_boundary([{'role':'user','content':'Prioritize the source dates.'}])
    delivered = await control.inspect(1,context.step_id)
    assert delivered['messages'][0]['status'] == 'delivered'
    assert delivered['messages'][0]['boundary_hash']
    assert await inbox.claim() == []
    phases = await control.events(1,context.step_id)
    notice = next(event for event in phases['events'] if event['phase'] == 'input_delivered')
    assert 'compliance requires result evidence' in notice['meaning']
    assert len([event for event in phases['events'] if event['phase'] == 'input_queued']) == 1
    await bits.coordinator.shutdown()


async def test_worker_event_artifacts_are_scoped_and_all_payload_fields_are_bounded(memory):
    bits,control = await environment(memory)
    parent,context,_ = await worker(memory,bits,control)
    original = {'phase':'tool_result','unrecognized_large_field':'私'*12000,'error':'x'*18000}
    await control.publish(context,original)
    page = await control.events(1,context.step_id)
    event = page['events'][-1]
    assert event['truncated'] and event['event_artifact']
    assert len(json.dumps(event,ensure_ascii=False).encode()) < 16000
    key = event['event_artifact']
    assert json.loads(await control.artifact(1,context.step_id,key)) == original
    _,other,_ = await worker(memory,bits,control,parent=parent,role='judge')
    with pytest.raises(CoordinationError): await control.artifact(1,other.step_id,key)
    with pytest.raises(CoordinationError): await control.artifact(2,context.step_id,key)
    await control.finish(context,'succeeded','証'*32000)
    final = (await control.events(1,context.step_id))['events'][-1]
    assert final['phase'] == 'finished' and final['truncated']
    assert len(json.dumps(final,ensure_ascii=False).encode()) < 16000
    raw = json.loads(await control.artifact(1,context.step_id,final['event_artifact']))
    assert raw['result'] == '証'*32000
    await bits.coordinator.shutdown()


async def test_misbound_worker_contexts_cannot_publish_finish_or_reenter(memory):
    bits,control = await environment(memory)
    _,context,_ = await worker(memory,bits,control)
    other_parent = await parent_context(memory,bits,2)
    forged = replace(context,user_id=2,room_id=other_parent.room_id,run_id=other_parent.run_id)
    for changed in (forged, replace(context,room_id=other_parent.room_id),
                    replace(context,run_id=other_parent.run_id),replace(context,agent_id='scourge'),
                    replace(context,task_role='interactive'),replace(context,step_id=uuid4().hex)):
        for operation in (control.publish(changed,{'phase':'text','text':'Forged'}),
                          control.finish(changed,'succeeded','Forged'),control.ensure_active(changed)):
            with pytest.raises(CoordinationError): await operation
    value = await control.inspect(1,context.step_id)
    assert value['stored_status'] == 'running' and value['result'] is None
    assert all(event.get('text') != 'Forged' for event in (await control.events(1,context.step_id))['events'])
    await bits.coordinator.shutdown()


async def test_interrupt_saves_request_but_unknown_effects_never_claim_cancelled_or_replay(memory):
    bits,control = await environment(memory)
    parent,context,_ = await worker(memory,bits,control)
    async with memory.sessions() as db,db.begin():
        db.add(ModelCallReceipt(id=uuid4().hex,run_id=parent.run_id,call_key=context.step_id+':0',
            request_hash='a'*64,status='unknown',route={},tokens=100,microdollars=100))
        db.add(ToolExecutionReceipt(user_id=1,request_id=context.step_id,call_id='effect',name='read',
            arguments_hash='a'*64,status='started'))
    with pytest.raises(CoordinationError,match='executor is unavailable'):
        await control.interrupt(1,context.step_id)
    value = await control.inspect(1,context.step_id)
    assert value['interrupt_requested'] and value['status'] == 'unknown'
    assert value['stored_status'] == 'running'
    await control.finish(context,'cancelled','Execution interrupted; outcome needs inspection')
    value = await control.inspect(1,context.step_id)
    assert value['stored_status'] == 'reconciling'
    duplicate = await control.begin(parent,context.step_id,'researcher','Find evidence, preserve attribution','kimi:kimi-k2.6',True)
    assert duplicate['duplicate'] and duplicate['status'] == 'reconciling'
    with pytest.raises(CoordinationError): await control.ensure_active(context)
    assert not bits.client.calls
    await bits.coordinator.shutdown()


async def test_forge_worker_view_and_controls_delegate_without_a_duplicate_execution_writer(memory):
    bits,control = await environment(memory)
    parent = await parent_context(memory,bits)
    project_id,task_id = uuid4().hex,uuid4().hex
    calls=[]
    class Projects:
        jobs={}
        async def steer(self,owner,execution_id,body):
            calls.append(('message',owner,execution_id,body.input_id,body.message))
            return {'id':'project-inbox-id','status':'queued'}
        async def stop_task(self,owner,execution_id):
            calls.append(('interrupt',owner,execution_id))
            async with memory.sessions() as db,db.begin():
                await db.execute(update(ProjectTask).where(ProjectTask.id == execution_id).values(cancelled=True,status='cancelled'))
    bits.coordinator.projects=Projects()
    async with memory.sessions() as db,db.begin():
        db.add(Project(id=project_id,user_id=1,name='Fixture',host_id='fixture',path='/fixture/project',legacy_id='fixture'))
        await db.flush()
        db.add(ProjectTask(id=task_id,project_id=project_id,run_id=parent.run_id,operation_id='fixture',
            parent_step=parent.step_id,owner_agent='speda',role='reviewer',task='Review the exact artifact',
            acceptance=['Read only'],dependencies=[],status='queued',result=None))
    first = await control.inspect(1,task_id)
    assert first['backend'] == 'forge' and first['worker'] == 'forge_reviewer'
    assert first['team'] == 'review' and first['supervisor_agent'] == 'speda'
    assert first['ticket_id'] == (await control.inspect(1,task_id))['ticket_id']
    message_id = uuid4().hex
    result = await control.message(1,task_id,'Focus on the changed lines.',message_id)
    assert result['status'] == 'queued' and calls[-1] == ('message',1,task_id,message_id,'Focus on the changed lines.')
    result = await control.interrupt(1,task_id)
    assert result['accepted'] and calls[-1] == ('interrupt',1,task_id)
    async with memory.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(LegionExecution)) == 0
        assert await db.scalar(select(func.count()).select_from(LegionInput)) == 0
        assert await db.scalar(select(func.count()).select_from(ProjectTask)) == 1
    assert not bits.client.calls
    await bits.coordinator.shutdown()


async def test_worker_cursor_pages_preserve_each_saved_event_once(memory):
    bits,control = await environment(memory)
    parent,context,_ = await worker(memory,bits,control)
    async with memory.sessions() as db,db.begin():
        room = await db.get(Room,parent.room_id)
        offset = room.sequence
        for index in range(205):
            db.add(RoomEvent(room_id=room.id,sequence=offset+index+1,run_id=parent.run_id,
                step_id=context.step_id,speaker='researcher',kind='worker_event',
                payload={'execution_id':context.step_id,'phase':'text','text':str(index)}))
        room.sequence = offset+205
    await control.finish(context,'succeeded','Collected findings')
    first = await control.events(1,context.step_id)
    assert len(first['events']) == 200 and first['next_cursor'] < first['last_sequence']
    second = await control.events(1,context.step_id,first['next_cursor'])
    assert len(second['events']) == 8
    assert not set(event['event_id'] for event in first['events']) & set(event['event_id'] for event in second['events'])
    frames = [json.loads(frame.removeprefix('data: ')) async for frame in control.subscribe(1,context.step_id) if frame.startswith('data: ')]
    assert len(frames) == 208
    assert len({frame['data']['event_id'] for frame in frames}) == 208
    assert frames[-1]['data']['phase'] == 'finished'
    assert not bits.client.calls
    await bits.coordinator.shutdown()


async def test_inbox_misbound_context_cannot_claim_or_ack_another_worker_input(memory):
    bits,control = await environment(memory)
    parent,context,_ = await worker(memory,bits,control)
    _,victim,_ = await worker(memory,bits,control,owner=2)
    message_id = uuid4().hex
    await control.message(2,victim.step_id,'Private owner-two guidance',message_id)
    forged = replace(context,step_id=victim.step_id)
    with pytest.raises(CoordinationError): await LegionInbox(control,forged).claim()
    async with memory.sessions() as db,db.begin():
        private = await db.scalar(select(LegionInput).where(LegionInput.execution_id == victim.step_id))
        private.status = 'delivering'
        private_id = private.id
    malicious = LegionInbox(control,context)
    malicious.claimed = [private_id]
    with pytest.raises(CoordinationError): await malicious.record_boundary([{'role':'user','content':'Forged acknowledgment'}])
    async with memory.sessions() as db:
        private = await db.get(LegionInput,private_id)
        assert private.status == 'delivering' and private.boundary_hash is None
    assert not bits.client.calls
    await bits.coordinator.shutdown()


async def test_lost_worker_lease_is_held_and_parent_resume_cannot_relaunch_it(memory):
    bits,control = await environment(memory)
    parent,context,_ = await worker(memory,bits,control)
    async with memory.sessions() as db,db.begin():
        await db.execute(update(CoordinationRun).where(CoordinationRun.id == parent.run_id).values(lease_until=time.time()-1))
    recovered = await bits.coordinator.recover()
    assert recovered['reconciling'] == 1
    value = await control.inspect(1,context.step_id)
    assert value['stored_status'] == 'reconciling' and not value['executor_available']
    with pytest.raises(CoordinationError): await bits.coordinator.resume(1,parent.run_id)
    assert not bits.client.calls
    await bits.coordinator.shutdown()


async def test_read_worker_admission_refuses_forge_roles_and_unknown_roles(memory):
    bits,control = await environment(memory)
    parent = await parent_context(memory,bits)
    for role in ('autobot','decepticon','forge_reviewer','unknown_worker'):
        with pytest.raises((CoordinationError,ValueError)):
            await control.begin(parent,uuid4().hex,role,'Do not create a second project writer','kimi:kimi-k2.6',False)
    async with memory.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(LegionExecution)) == 0
    await bits.coordinator.shutdown()


async def test_stop_before_worker_final_delivery_cannot_become_success(memory):
    bits,control=await environment(memory)
    _,context,_=await worker(memory,bits,control)
    async with memory.sessions() as db,db.begin():
        await db.execute(update(LegionExecution).where(LegionExecution.id==context.step_id).values(cancelled=True))
    assert await control.finish(context,'succeeded','Confirmed partial finding')=='interrupted'
    assert (await control.inspect(1,context.step_id))['status']=='interrupted'
    with pytest.raises(CoordinationError):await control.publish(context,{'phase':'text','text':'Late result'})
    assert await control.finish(context,'succeeded','Do not overwrite the earlier result')=='interrupted'
    assert (await control.inspect(1,context.step_id))['result']=='Confirmed partial finding'
    await bits.coordinator.shutdown()


async def test_admitted_worker_can_be_cancelled_without_starting_an_executor(memory):
    bits,control=await environment(memory)
    parent=await parent_context(memory,bits)
    execution_id=uuid4().hex
    await control.begin(parent,execution_id,'researcher','Do not start after cancellation','kimi:kimi-k2.6',True)
    receipt=await control.interrupt(1,execution_id)
    assert receipt['accepted'] and receipt['execution']['stored_status']=='interrupted'
    child=replace(parent,step_id=execution_id,task_role='worker')
    assert not await control.start(child)
    assert not bits.client.calls
    await bits.coordinator.shutdown()


async def test_root_stop_settles_unstarted_admissions_with_a_durable_event(memory):
    bits,control=await environment(memory)
    parent=await parent_context(memory,bits)
    execution_id=uuid4().hex
    await control.begin(parent,execution_id,'researcher','Pending dispatch','kimi:kimi-k2.6',True)
    await control.stop_run(parent.run_id)
    page=await control.events(1,execution_id)
    assert page['execution']['stored_status']=='interrupted'
    assert page['events'][-1]['phase']=='finished' and page['events'][-1]['status']=='interrupted'
    assert not bits.client.calls
    await bits.coordinator.shutdown()
