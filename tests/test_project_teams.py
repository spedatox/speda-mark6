"""Native project team attribution, immutable binding and scoped task control."""
import asyncio
from types import SimpleNamespace
import time

import pytest
from sqlalchemy import select

from app.models.coordination import AgentStep, CoordinationRun, ProjectTask
from app.schemas.coordination import CreateProject, CreateRoom, Delegate, RoomInput
from app.schemas.llm import Block, LLMMessage
from app.services.coordination_budget import CoordinationError
from forge.runtime import ExecutionResult
from tests.test_coordination import reply, send
from tests.test_projects import delegate, project_setup


async def admitted_context(memory, bits, *, agent="speda"):
    room = await bits.coordinator.create(1, CreateRoom(lead=agent, project_id=bits.project['id']))
    bits.coordinator.launch = lambda _: None
    receipt = await bits.coordinator.start(1, room['id'], RoomInput(input_id='team-request', message='Prepare bounded project work'))
    async with memory.sessions() as db, db.begin():
        run = await db.get(CoordinationRun, receipt['run_id'])
        run.status, run.executor, run.lease_until = 'running', bits.coordinator.identity, time.time() + 60
        step = await db.scalar(select(AgentStep).where(AgentStep.run_id == run.id))
    return memory.context(agent_id=agent, room_id=room['id'], run_id=run.id,
        step_id=step.id, project_id=bits.project['id'], tool_call_id='assign', coordination=bits.coordinator), room


@pytest.mark.parametrize('agent,role,runtime', [
    ('optimus', 'autobot', 'coder'),
    ('scourge', 'decepticon', 'pentester'),
])
async def test_standalone_forge_executes_without_binding_chat(memory, tmp_path, agent, role, runtime):
    async def executor(spec, **kwargs):
        assert spec.role == runtime and not spec.allow_network
        assert not list(spec.workspace.iterdir())
        return ExecutionResult(spec.job_id, 'succeeded', 'Standalone check completed')

    assignment = delegate(role) if agent == 'optimus' else LLMMessage([
        Block(type='tool_use', id='delegate1', name='Task',
              input={'worker_id': role, 'description': 'Check tools', 'prompt': 'Check local tooling'}),
    ], 'tool_use')
    bits = await project_setup(memory, tmp_path, [assignment, reply('Queued')], executor)
    bits.projects.settings.project_supervision_enabled = True
    room = await bits.coordinator.create(1, CreateRoom(lead=agent))
    try:
        result = await send(bits, room)
        assert result['status'] == 'succeeded'
        events = (await bits.coordinator.events(1, room['id']))['events']
        task = next(e['payload'] for e in events if e['kind'] == 'project_result')
        assert task['role'] == role
        assert task['artifact']['report'] == 'Standalone check completed'
        assert not any(e['payload'].get('mode') == 'mission_review' for e in events)
        assert not (await bits.projects.inspect(1, bits.project['id']))['tasks']
        assert (await bits.coordinator.rooms(1))[0]['project_id'] is None
        with pytest.raises(CoordinationError, match='configured workspace root'):
            await bits.coordinator.create(1, CreateRoom(lead=agent, project_id=task['project_id']))
    finally:
        await bits.coordinator.shutdown()


@pytest.mark.parametrize('agent,role,team,lead,runtime,read_only', [
    ('optimus','autobot','autobots','optimus','coder',False),
    ('scourge','decepticon','decepticons','scourge','pentester',False),
    ('optimus','reviewer','review',None,'reviewer',True),
])
async def test_named_teams_report_real_supervisor_and_separate_worker_role(memory,tmp_path,agent,role,team,lead,runtime,read_only):
    seen=[]
    async def executor(spec, *, emit, **kwargs):
        seen.append(spec)
        event=SimpleNamespace(type='progress',data={'message':'Inspected the supplied artifact'})
        event.model_dump=lambda: {'type':event.type,'data':event.data}
        await emit(event)
        return ExecutionResult(spec.job_id,'succeeded','Bounded worker report; source remains unchanged')
    bits=await project_setup(memory,tmp_path,[delegate(role),reply('Work is queued')],executor)
    room=await bits.coordinator.create(1,CreateRoom(lead=agent,project_id=bits.project['id']))
    try:
        result=await send(bits,room)
        assert result['status']=='succeeded'
        task=(await bits.projects.inspect(1,bits.project['id']))['tasks'][0]
        assert task['task']=='Update the example' and task['acceptance']==['Keep the canonical project intact']
        assert task['owner_agent']==task['supervisor_agent']==agent
        assert (task['team'],task['team_lead'],task['runtime_role'],task['read_only'])==(team,lead,runtime,read_only)
        assert task['parent_step'] and task['task_id']==task['id'] and not task['cancel_requested']
        assert seen[0].role==runtime and not seen[0].allow_network
        assert bits.path.joinpath('example.txt').read_text()=='original'
        events=(await bits.coordinator.events(1,room['id']))['events']
        projected=[e for e in events if e['kind'] in {'project_queued','project_started','project_event','project_result'}]
        assert {e['kind'] for e in projected}=={'project_queued','project_started','project_event','project_result'}
        for event in projected:
            payload=event['payload']
            assert payload['task_id']==task['id'] and payload['owner_agent']==agent
            assert payload['role']==role and payload['team']==team and payload['project_id']==bits.project['id']
    finally:
        await bits.coordinator.shutdown()


@pytest.mark.parametrize('agent,role', [('optimus','decepticon'),('scourge','autobot'),('friday','autobot')])
async def test_team_authority_cannot_be_expanded_by_assignment_prose(memory,tmp_path,agent,role):
    async def executor(*args,**kwargs): raise AssertionError('Unauthorized worker executed')
    bits=await project_setup(memory,tmp_path,[],executor)
    context,_=await admitted_context(memory,bits,agent=agent)
    try:
        with pytest.raises(CoordinationError,match='cannot assign'):
            await bits.projects.delegate(context,Delegate(role=role,task='Claim authority over another team',acceptance=['Stay scoped']))
        assert not (await bits.projects.inspect(1,bits.project['id']))['tasks']
    finally:
        await bits.coordinator.shutdown()


async def test_speda_cross_team_admission_preserves_actual_delegator(memory,tmp_path):
    async def executor(*args,**kwargs): raise AssertionError('This test only admits work')
    bits=await project_setup(memory,tmp_path,[],executor)
    context,_=await admitted_context(memory,bits)
    try:
        for role in ('autobot','decepticon'):
            context.tool_call_id=role
            await bits.projects.delegate(context,Delegate(role=role,task='Prepare authorized work',acceptance=['Keep attribution']))
        tasks=(await bits.projects.inspect(1,bits.project['id']))['tasks']
        assert {t['team_lead'] for t in tasks}=={'optimus','scourge'}
        assert all(t['supervisor_agent']==t['owner_agent']=='speda' for t in tasks)
    finally:
        await bits.coordinator.shutdown()


async def test_assignments_are_binding_fenced_idempotent_and_workers_cannot_delegate(memory,tmp_path):
    async def executor(*args,**kwargs): raise AssertionError('This test only admits work')
    bits=await project_setup(memory,tmp_path,[],executor)
    context,_=await admitted_context(memory,bits)
    body=Delegate(role='autobot',task='Implement bounded change',acceptance=['Keep source original'])
    try:
        first=await bits.projects.delegate(context,body)
        assert await bits.projects.delegate(context,body)==first
        with pytest.raises(CoordinationError,match='another assignment'):
            await bits.projects.delegate(context,body.model_copy(update={'task':'Replace the admitted task'}))
        context.task_role='worker'
        with pytest.raises(CoordinationError,match='cannot start'):
            await bits.projects.delegate(context,body)
        context.task_role='interactive'
        other=bits.path.parent/'another'
        other.mkdir()
        other.joinpath('example.txt').write_text('other project',encoding='utf-8')
        registered=await bits.projects.create(1,CreateProject(name='Other',path=str(other)))
        context.project_id=registered['id']
        context.tool_call_id='retarget'
        with pytest.raises(CoordinationError,match='immutable'):
            await bits.projects.delegate(context,body)
        assert not (await bits.projects.inspect(1,registered['id']))['tasks']
    finally:
        await bits.coordinator.shutdown()


async def test_terminal_task_stop_does_not_fabricate_cancel_request(memory,tmp_path):
    async def executor(spec,**kwargs): return ExecutionResult(spec.job_id,'succeeded','Already complete')
    bits=await project_setup(memory,tmp_path,[delegate(),reply('Queued')],executor)
    room=await bits.coordinator.create(1,CreateRoom(lead='optimus',project_id=bits.project['id']))
    try:
        await send(bits,room)
        task=(await bits.projects.inspect(1,bits.project['id']))['tasks'][0]
        stopped=await bits.projects.stop_task(1,task['id'])
        assert not stopped['task_control']['accepted']
        assert stopped['tasks'][0]['status']=='succeeded' and not stopped['tasks'][0]['cancel_requested']
        events=(await bits.coordinator.events(1,room['id']))['events']
        assert not any(e['kind']=='task_cancel_requested' for e in events)
    finally:
        await bits.coordinator.shutdown()


async def test_queued_task_cancellation_has_scoped_durable_attribution(memory,tmp_path):
    async def executor(*args,**kwargs): raise AssertionError('Cancelled task executed')
    bits=await project_setup(memory,tmp_path,[],executor)
    context,room=await admitted_context(memory,bits,agent='scourge')
    try:
        task=await bits.projects.delegate(context,Delegate(role='decepticon',task='Review local security',acceptance=['No network']))
        stopped=await bits.projects.stop_task(1,task['task_id'])
        assert stopped['task_control']=={'task_id':task['task_id'],'accepted':True,'scope':'task'}
        row=stopped['tasks'][0]
        assert row['status']=='cancelled' and row['cancel_requested']
        events=(await bits.coordinator.events(1,room['id']))['events']
        request=next(e for e in events if e['kind']=='task_cancel_requested')
        assert request['payload']['owner_agent']=='scourge' and request['payload']['scope']=='task'
        assert request['payload']['task_id']==task['task_id']
        async with memory.sessions() as db:
            assert not (await db.get(CoordinationRun,context.run_id)).cancelled
        await bits.projects.drain(context.run_id)
        assert (await bits.coordinator.inspect_run(1,context.run_id))['status']=='partial'
    finally:
        await bits.coordinator.shutdown()


async def test_project_worker_model_choice_uses_admitted_snapshot(memory,tmp_path):
    seen=[]
    async def executor(spec,**kwargs):
        seen.append(spec.model_ref)
        return ExecutionResult(spec.job_id,'succeeded','Inspected the pinned route')
    bits=await project_setup(memory,tmp_path,[],executor)
    revision,policy=await bits.policy.snapshot(1)
    policy.worker_models['autobot']='openai:fixture-before-admission'
    await bits.policy.save(1,revision,policy)
    context,room=await admitted_context(memory,bits,agent='optimus')
    revision,policy=await bits.policy.snapshot(1)
    policy.worker_models['autobot']='openai:fixture-after-admission'
    await bits.policy.save(1,revision,policy)
    try:
        await bits.projects.delegate(context,Delegate(role='autobot',task='Check pinned model',acceptance=['No silent route change']))
        await bits.projects.drain(context.run_id)
        assert seen==['openai:fixture-before-admission']
        events=(await bits.coordinator.events(1,room['id']))['events']
        started=next(e for e in events if e['kind']=='project_started')['payload']
        assert started['model']=='openai:fixture-before-admission'
        assert started['policy_revision']==revision
        assert started['thinking']=={'mode':'provider_default','value':None}
    finally:
        await bits.coordinator.shutdown()
