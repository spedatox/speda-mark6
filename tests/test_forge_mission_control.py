"""Live IGOR/FORGE exchanges through native loops and durable SQLite records."""
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import sys
import time
from uuid import uuid4
import pytest
from sqlalchemy import select, update
from app.models.coordination import AgentStep, CoordinationRun, ProjectTask, SupervisorInput, WorkerReview, ProjectOperation, Room
from app.schemas.coordination import CreateRoom, RoomInput, Steering, Delegate, ArtifactReview, PrepareRelease, ApproveRelease
from app.schemas.llm import LLMMessage, Block
from app.services.coordination_budget import CoordinationError, ReplayStream
from app.services.mission_control import MissionControl, SupervisorInbox
from app.services.project_release import ProjectRelease
from app.services.forge_adapter import ForgeEffects
from app.services.task_inbox import TaskInbox
from forge.model.base import ToolUseRequest
from forge.runtime import ExecutionResult
from forge.warden.tool import ToolResult
from tests.test_coordination import reply, send
from tests.test_projects import project_setup, delegate
from tests.test_project_teams import admitted_context


async def test_forge_runs_before_reply_finishes_and_later_chat_steers_same_worker(memory, tmp_path):
    started, release = asyncio.Event(), asyncio.Event()
    async def executor(spec, *, signal, **kwargs):
        started.set()
        await release.wait()
        return ExecutionResult(spec.job_id, 'succeeded', 'Work completed without restarting')
    bits = await project_setup(memory, tmp_path, [delegate(), reply('Worker active'), reply('Prioritize accessibility')], executor)
    from app.services.worker_control import WorkerControl
    from app.skills.legion import LegionControlSkill
    bits.coordinator.workers = WorkerControl(bits.coordinator)
    await bits.engine._registry.register_skill(LegionControlSkill())
    original = bits.client.stream_message
    @asynccontextmanager
    async def stream(**args):
        if len(bits.client.calls) == 1:
            await asyncio.wait_for(started.wait(), 5)
        async with original(**args) as result:
            yield result
    bits.client.stream_message = stream
    room = await bits.coordinator.create(1, CreateRoom(lead='optimus', project_id=bits.project['id']))
    first = await bits.coordinator.start(1, room['id'], RoomInput(input_id='build', message='Build the project'))
    first_job = bits.coordinator.live[first['run_id']]
    try:
        await asyncio.wait_for(started.wait(), 5)
        # Wait only for the public round to release the conversation, not the worker.
        for _ in range(100):
            if (await bits.coordinator.inspect_run(1, first['run_id']))['status'] == 'waiting_tasks':
                break
            await asyncio.sleep(.02)
        assert not first_job.done()
        async with memory.sessions() as db:
            task_id = await db.scalar(select(ProjectTask.id).where(ProjectTask.run_id == first['run_id']))
        bits.client.script[-1] = LLMMessage([Block(type='tool_use', id='redirect', name='legion_control',
            input={'execution_id': task_id, 'action': 'message', 'text': 'Prioritize accessibility', 'message_id': uuid4().hex})], 'tool_use')
        bits.client.script.append(reply('The active Autobot has the updated requirement'))
        second = await send(bits, room, 'Prioritize accessibility in the active work', input_id='redirect')
        assert second['status'] == 'succeeded' and not first_job.done()
        assert 'Live mission state' in str(bits.client.calls[2]['messages'])
        live = await bits.coordinator.workers.inspect(1, task_id)
        assert live['stored_status'] == 'running' and live['messages'][0]['status'] == 'queued'
        assert 'Supervisor agent optimus' in live['messages'][0]['text']
        release.set()
        await asyncio.wait_for(first_job, 5)
        assert (await bits.coordinator.workers.inspect(1, task_id))['stored_status'] == 'succeeded'
    finally:
        release.set()
        await bits.coordinator.shutdown()


async def test_owner_can_steer_active_igor_reply_without_losing_context(memory, tmp_path):
    entered, finish = asyncio.Event(), asyncio.Event()
    async def executor(*args, **kwargs):
        raise AssertionError('Lightweight reasoning should not dispatch FORGE')
    bits = await project_setup(memory, tmp_path, [reply('Original reasoning'), reply('Updated reasoning')], executor)
    original = bits.client.stream_message
    @asynccontextmanager
    async def stream(**args):
        if not bits.client.calls:
            entered.set()
            await finish.wait()
        async with original(**args) as result:
            yield result
    bits.client.stream_message = stream
    room = await bits.coordinator.create(1, CreateRoom(lead='scourge'))
    admitted = await bits.coordinator.start(1, room['id'], RoomInput(input_id='threat-model', message='Explain the trust boundary'))
    job = bits.coordinator.live[admitted['run_id']]
    try:
        await asyncio.wait_for(entered.wait(), 5)
        control = MissionControl(bits.coordinator)
        body = Steering(input_id='correction', message='Focus on offline threat modeling')
        accepted = await control.steer(1, admitted['run_id'], body)
        assert (await control.steer(1, admitted['run_id'], body))['id'] == accepted['id']
        with pytest.raises(CoordinationError):
            await control.steer(2, admitted['run_id'], body)
        with pytest.raises(CoordinationError, match='another instruction'):
            await control.steer(1, admitted['run_id'], body.model_copy(update={'message': 'Different'}))
        finish.set()
        await asyncio.wait_for(job, 5)
        assert len(bits.client.calls) == 2
        assert 'Focus on offline threat modeling' in str(bits.client.calls[1]['messages'])
        async with memory.sessions() as db:
            item = await db.get(SupervisorInput, accepted['id'])
            assert item.status == 'delivered' and item.boundary_hash
        assert not (await bits.projects.inspect(1, bits.project['id']))['tasks']
    finally:
        finish.set()
        await bits.coordinator.shutdown()


async def test_pending_steering_prevents_stale_tool_effect_and_survives_reconstruction(memory, tmp_path):
    async def executor(*args, **kwargs):
        raise AssertionError('Not dispatched')
    bits = await project_setup(memory, tmp_path, [], executor)
    parent, room = await admitted_context(memory, bits, agent='optimus')
    item = await bits.projects.delegate(parent, Delegate(role='autobot', task='Write', acceptance=['Keep scoped']))
    task_id = item['task_id']
    async with memory.sessions() as db, db.begin():
        await db.execute(update(ProjectTask).where(ProjectTask.id == task_id).values(status='running'))
    context = memory.context(agent_id='optimus', coordination=bits.coordinator, task_role='worker',
        project_id=bits.project['id'], room_id=room['id'], run_id=parent.run_id, step_id=task_id)
    context.request_id = task_id
    try:
        await bits.projects.steer(1, task_id, Steering(input_id='new-scope', message='Read before writing'))
        called = []
        async def effect():
            called.append(True)
            return ToolResult('write happened')
        result = await ForgeEffects(memory.sessions, context)(ToolUseRequest('stale-write', 'write_file', {'path': 'x'}), effect)
        assert result.is_error and not called
        inbox = TaskInbox(context, memory.sessions)
        assert await inbox.claim() == ['Read before writing']
        await inbox.record_boundary([{'role': 'user', 'content': 'Read before writing'}])
        assert await TaskInbox(context, memory.sessions).claim() == []
        # A fresh call can run after the instruction has been delivered.
        assert not (await ForgeEffects(memory.sessions, context)(ToolUseRequest('replanned', 'write_file', {'path': 'x'}), effect)).is_error
        assert called == [True]
    finally:
        await bits.coordinator.shutdown()


async def completed_artifact(memory, tmp_path):
    async def executor(spec, **kwargs):
        (spec.workspace / 'example.txt').write_text('verified release')
        return ExecutionResult(spec.job_id, 'succeeded', 'Changed the example')
    bits = await project_setup(memory, tmp_path, [], executor)
    context, room = await admitted_context(memory, bits, agent='optimus')
    receipt = await bits.projects.delegate(context, Delegate(role='autobot', task='Build release', acceptance=['Retain source']))
    await bits.projects.drain(context.run_id)
    async with memory.sessions() as db, db.begin():
        task = await db.get(ProjectTask, receipt['task_id'])
        run = await db.get(CoordinationRun, context.run_id)
        run.status = 'running'
    return bits, context, room, task


async def test_reviewed_release_executes_real_driver_only_after_exact_owner_approval(memory, tmp_path):
    bits, context, room, task = await completed_artifact(memory, tmp_path)
    control = MissionControl(bits.coordinator)
    release = ProjectRelease(bits.projects)
    destination = tmp_path / 'published.txt'
    driver = tmp_path / 'deploy.py'
    driver.write_text('import pathlib,sys\npathlib.Path(sys.argv[2]).write_bytes((pathlib.Path(sys.argv[1])/"example.txt").read_bytes())\n')
    verify = tmp_path / 'verify.py'
    verify.write_text('import pathlib,sys\nassert pathlib.Path(sys.argv[1]).read_text()=="verified release"\n')
    targets = tmp_path / 'targets.json'
    targets.write_text(json.dumps({'fixture': {'description': 'Publish fixture artifact locally',
        'deploy': [sys.executable, str(driver), '{workspace}', str(destination)],
        'verify': [sys.executable, str(verify), str(destination)],
        'rollback_instructions': 'Restore the retained previous fixture release'}}))
    bits.projects.settings.project_release_targets_file = str(targets)
    body = PrepareRelease(task_id=task.id, artifact_revision=task.result['revision'], target='fixture')
    try:
        with pytest.raises(CoordinationError, match='supervisor acceptance'):
            await release.prepare(context, body)
        assessment = ArtifactReview(task_id=task.id, artifact_revision=task.result['revision'],
            expected_version=0, verdict='accepted', rationale='Reviewed changed file against fixture acceptance')
        await control.review(context, assessment)
        plan = await release.prepare(context, body)
        assert plan['status'] == 'awaiting_approval' and not destination.exists()
        assert plan['plan']['changes'][0]['path'] == 'example.txt'
        assert plan['plan']['changes'][0]['change'] == 'modify'
        assert 'verified release' in plan['plan']['diff']
        assert Path(plan['plan']['baseline_workspace']).joinpath('example.txt').read_text() == 'original'
        assert bits.path.joinpath('example.txt').read_text() == 'original'
        with pytest.raises(CoordinationError):
            await release.decide(2, plan['id'], ApproveRelease(plan_hash=plan['plan_hash'], decision='approve'))
        with pytest.raises(CoordinationError, match='exact reviewed plan'):
            await release.decide(1, plan['id'], ApproveRelease(plan_hash='0'*64, decision='approve'))
        decision = ApproveRelease(plan_hash=plan['plan_hash'], decision='approve')
        deployed = await release.decide(1, plan['id'], decision)
        assert deployed['status'] == 'complete' and destination.read_text() == 'verified release'
        assert [r['returncode'] for r in deployed['outcomes']] == [0, 0]
        destination.write_text('owner changed deployment')
        assert (await ProjectRelease(bits.projects).decide(1, plan['id'], decision))['status'] == 'complete'
        assert destination.read_text() == 'owner changed deployment'  # Never replay an approval.
        assert Path(plan['plan']['workspace']).joinpath('example.txt').read_text() == 'verified release'
    finally:
        await bits.coordinator.shutdown()


async def test_automatic_supervisor_acceptance_uses_original_budget_and_tools(memory, tmp_path):
    async def executor(spec, **kwargs):
        return ExecutionResult(spec.job_id, 'succeeded', 'Read the exact artifact without changing it')
    bits = await project_setup(memory, tmp_path, [delegate(), reply('Autobot working')], executor)
    bits.projects.settings.project_supervision_enabled = True
    from app.skills.collaboration import ProjectReviewSkill
    await bits.engine._registry.register_skill(ProjectReviewSkill())
    original = bits.client.stream_message
    @asynccontextmanager
    async def stream(**args):
        if not bits.client.script:
            async with memory.sessions() as db:
                task = await db.scalar(select(ProjectTask).where(ProjectTask.status == 'succeeded'))
            if len(bits.client.calls) == 2:
                bits.client.script.append(LLMMessage([Block(type='tool_use', id='accept-artifact', name='project_review', input={
                    'task_id': task.id, 'artifact_revision': task.result['revision'], 'expected_version': 0,
                    'verdict': 'accepted', 'rationale': 'Reviewed the retained unchanged file; no runtime checks claimed', 'checks': []})], 'tool_use'))
            else:
                bits.client.script.append(reply('Artifact assessed; publication still needs your approval'))
        async with original(**args) as result:
            yield result
    bits.client.stream_message = stream
    room = await bits.coordinator.create(1, CreateRoom(lead='optimus', project_id=bits.project['id']))
    try:
        result = await send(bits, room)
        assert result['status'] == 'succeeded', result
        assert result['reserved']['calls'] == 4 and len(result['steps']) == 2
        async with memory.sessions() as db:
            review = await db.scalar(select(WorkerReview))
            assert review.verdict == 'accepted' and review.reviewer_step == result['steps'][1]['id']
        assert 'Evaluate completed FORGE assignment' in str(bits.client.calls[2]['messages'])
    finally:
        await bits.coordinator.shutdown()


async def test_supervisor_steering_restores_exact_reasoning_boundary(memory, tmp_path):
    async def executor(*args, **kwargs):
        raise AssertionError('Reasoning only')
    bits = await project_setup(memory, tmp_path, [], executor)
    context, room = await admitted_context(memory, bits, agent='optimus')
    async with memory.sessions() as db, db.begin():
        step = await db.get(AgentStep, context.step_id)
        step.status = 'running'
    control = MissionControl(bits.coordinator)
    try:
        await control.steer(1, context.run_id, Steering(input_id='durable-owner-input', message='Keep all accessibility features'))
        inbox = SupervisorInbox(control, context)
        assert 'Keep all accessibility features' in str(await inbox.claim())
        context.model_call_index = 3
        messages = [{'role': 'user', 'content': 'Original request'}, {'role': 'assistant', 'content': 'Prior reasoning'},
            {'role': 'user', 'content': 'Keep all accessibility features'}]
        await inbox.record_boundary(messages)
        from app.models.user import User
        async with memory.sessions() as db:
            run, step, bound, user = (await db.get(CoordinationRun, context.run_id),
                await db.get(AgentStep, context.step_id), await db.get(Room, room['id']), await db.get(User, 1))
        restored = await bits.coordinator.context_for(run, step, bound, user)
        assert restored.model_call_index == 3 and restored.conversation_history == messages
        assert not await restored.extra['supervisor_inbox'].claim(observations=False)
    finally:
        await bits.coordinator.shutdown()


@pytest.mark.parametrize('mutation,code', [('artifact','artifact_changed'), ('source','source_changed'),
    ('target','release_target_changed'), ('review','review_changed')])
async def test_release_approval_rejects_drift_without_executing(memory, tmp_path, mutation, code):
    bits, context, room, task = await completed_artifact(memory, tmp_path)
    await MissionControl(bits.coordinator).review(context, ArtifactReview(task_id=task.id,
        artifact_revision=task.result['revision'], expected_version=0, verdict='accepted', rationale='Reviewed fixture'))
    marker = tmp_path / 'should-not-exist'
    driver = tmp_path / 'driver.py'
    driver.write_text('import pathlib,sys\npathlib.Path(sys.argv[1]).write_text("ran")\n')
    targets = tmp_path / 'targets.json'
    config = {'fixture': {'description':'Fixture release', 'deploy':[sys.executable,str(driver),str(marker)],
        'verify':[sys.executable,'-c','pass'], 'rollback_instructions':'Restore fixture baseline'}}
    targets.write_text(json.dumps(config))
    bits.projects.settings.project_release_targets_file = str(targets)
    service = ProjectRelease(bits.projects)
    plan = await service.prepare(context, PrepareRelease(task_id=task.id, artifact_revision=task.result['revision'], target='fixture'))
    if mutation == 'artifact':
        Path(plan['plan']['workspace']).joinpath('example.txt').write_text('changed')
    elif mutation == 'source':
        bits.path.joinpath('example.txt').write_text('owner live edit')
    elif mutation == 'target':
        config['fixture']['description'] = 'Different target'
        targets.write_text(json.dumps(config))
    else:
        await MissionControl(bits.coordinator).review(context, ArtifactReview(task_id=task.id,
            artifact_revision=task.result['revision'], expected_version=1, verdict='needs_changes', rationale='Found a missing check'))
    try:
        with pytest.raises(CoordinationError) as error:
            await service.decide(1, plan['id'], ApproveRelease(plan_hash=plan['plan_hash'],decision='approve'))
        assert error.value.code == code
        assert not marker.exists()
        assert (await service.inspect(1,plan['id']))['status'] == 'awaiting_approval'
    finally:
        await bits.coordinator.shutdown()


async def test_failed_health_check_is_held_and_reconciled_without_redeploying(memory, tmp_path):
    from app.schemas.coordination import ReconcileRelease
    bits, context, room, task = await completed_artifact(memory, tmp_path)
    await MissionControl(bits.coordinator).review(context, ArtifactReview(task_id=task.id,
        artifact_revision=task.result['revision'], expected_version=0, verdict='accepted', rationale='Reviewed fixture'))
    targets = tmp_path / 'targets.json'
    targets.write_text(json.dumps({'fixture': {'description':'Health failure fixture',
        'deploy':[sys.executable,'-c','print("deploy completed")'],
        'verify':[sys.executable,'-c','raise SystemExit(3)'], 'rollback_instructions':'Restore the retained previous fixture release'}}))
    bits.projects.settings.project_release_targets_file = str(targets)
    service = ProjectRelease(bits.projects)
    plan = await service.prepare(context, PrepareRelease(task_id=task.id, artifact_revision=task.result['revision'], target='fixture'))
    decision = ApproveRelease(plan_hash=plan['plan_hash'],decision='approve')
    try:
        with pytest.raises(CoordinationError, match='health verification failed'):
            await service.decide(1, plan['id'], decision)
        held = await service.inspect(1, plan['id'])
        assert held['status'] == 'reconciling'
        assert [r['returncode'] for r in held['outcomes']] == [0,3]
        assert (await ProjectRelease(bits.projects).decide(1, plan['id'], decision))['outcomes'] == held['outcomes']
        resolved = await service.reconcile(1, plan['id'], ReconcileRelease(plan_hash=plan['plan_hash'],
            confirmed_driver_stopped=True, outcome='rolled_back', evidence='Operator inspected the fixture and restored its previous version'))
        assert resolved['status'] == 'failed' and resolved['operator_reconciliation']['outcome'] == 'rolled_back'
    finally:
        await bits.coordinator.shutdown()


async def test_later_turn_can_continue_from_exact_predecessor_artifact(memory, tmp_path):
    bits, parent, room, task = await completed_artifact(memory, tmp_path)
    from app.models.user import User
    async with memory.sessions() as db, db.begin():
        bound = await db.get(Room, room['id'])
        bound.active_run = None
    admitted = await bits.coordinator.start(1, room['id'], RoomInput(input_id='later-review', message='Review the prior artifact'))
    async with memory.sessions() as db, db.begin():
        run = await db.get(CoordinationRun, admitted['run_id'])
        run.status, run.executor, run.lease_until = 'running', bits.coordinator.identity, time.time()+60
        step = await db.scalar(select(AgentStep).where(AgentStep.run_id == run.id))
        step.status = 'running'
        bound, user = await db.get(Room, room['id']), await db.get(User, 1)
    context = await bits.coordinator.context_for(run, step, bound, user)
    context.tool_call_id = 'review-prior-artifact'
    seen = []
    async def reviewer(spec, **kwargs):
        seen.append((spec.role, spec.workspace.joinpath('example.txt').read_text()))
        return ExecutionResult(spec.job_id, 'succeeded', 'Reviewed exact prior artifact')
    bits.projects.executor = reviewer
    try:
        followup = await bits.projects.delegate(context, Delegate(role='reviewer', task='Review previous work',
            acceptance=['Read retained artifact'], dependencies=[task.id]))
        await bits.projects.drain(run.id)
        assert seen == [('reviewer','verified release')]
        async with memory.sessions() as db:
            result = await db.get(ProjectTask, followup['task_id'])
            assert result.base_revision == task.result['revision'] and result.run_id != task.run_id
        assert bits.path.joinpath('example.txt').read_text() == 'original'
    finally:
        await bits.coordinator.shutdown()


async def test_native_approval_trays_are_authenticated_and_never_remember_publication(memory, tmp_path):
    import httpx
    from app.main import create_app
    bits, context, room, task = await completed_artifact(memory, tmp_path)
    await MissionControl(bits.coordinator).review(context, ArtifactReview(task_id=task.id,
        artifact_revision=task.result['revision'], expected_version=0, verdict='accepted', rationale='Reviewed fixture'))
    targets = tmp_path / 'targets.json'
    targets.write_text(json.dumps({'fixture': {'description':'Approval UI fixture',
        'deploy':[sys.executable,'-c','raise AssertionError("Must not deploy")'],
        'verify':[sys.executable,'-c','pass'], 'rollback_instructions':'Retain the fixture baseline'}}))
    bits.projects.settings.project_release_targets_file = str(targets)
    service = ProjectRelease(bits.projects)
    plan = await service.prepare(context, PrepareRelease(task_id=task.id, artifact_revision=task.result['revision'], target='fixture'))
    app = create_app()
    app.state.projects, app.state.coordinator = bits.projects, bits.coordinator
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture') as client:
            assert (await client.get('/agents/asks')).status_code == 401
            assert (await client.post('/coordination/releases/'+plan['id']+'/decision',json={
                'plan_hash':plan['plan_hash'],'decision':'approve'})).status_code == 401
            client.headers['X-API-Key'] = 'test-only-key'
            ask = (await client.get('/agents/asks')).json()[0]
            assert plan['plan_hash'] in ask['ask_id'] and 'baseline_workspace' in ask['action_key']
            assert (await client.post('/agents/asks/'+ask['ask_id'], json={'approved':True,'remember':True})).status_code == 422
            result = await client.post('/agents/asks/'+ask['ask_id'],json={'approved':False,'remember':False,'note':''})
            assert result.status_code == 200 and result.json()['status'] == 'rejected'
            assert (await client.get('/agents/asks')).json() == []
    finally:
        await bits.coordinator.shutdown()


async def test_session_stream_keeps_supervisor_assessments_after_task_completion(memory, tmp_path):
    from app.services.worker_control import WorkerControl
    bits, context, room, task = await completed_artifact(memory, tmp_path)
    workers = WorkerControl(bits.coordinator)
    bits.coordinator.workers = workers
    try:
        with pytest.raises(CoordinationError):
            await workers.session_room(2, context.session_id)
        # The test context is synthesized; use the actual bound session identity.
        async with memory.sessions() as db:
            bound = await db.get(Room, room['id'])
        before = (await bits.coordinator.events(1, room['id']))['next_cursor']
        await MissionControl(bits.coordinator).review(context, ArtifactReview(task_id=task.id,
            artifact_revision=task.result['revision'], expected_version=0, verdict='accepted', rationale='Checked the retained artifact'))
        for text in ('Review evidence one', 'Review evidence two'):
            await bits.coordinator.emit(context.run_id, 'project_event', 'autobot',
                {'task_id': task.id, 'event': {'type': 'chunk', 'data': text}})
        inspected = []
        original_inspect = workers.inspect
        async def counted_inspect(owner, execution_id):
            inspected.append(execution_id)
            return await original_inspect(owner, execution_id)
        workers.inspect = counted_inspect
        stream = workers.subscribe_session(1, bound.session_id, before)
        frame = json.loads((await anext(stream)).removeprefix('data: '))
        assert frame['data']['execution_id'] == task.id
        assert frame['data']['assessment'] == 'accepted'
        assert 'Supervisor assessment by optimus' in frame['data']['text']
        assert 'Review evidence one' in await anext(stream)
        assert 'Review evidence two' in await anext(stream)
        assert inspected == [task.id]  # One metadata projection per worker per event page.
        await stream.aclose()
        page = await workers.events(1, task.id, before)
        assert page['next_cursor'] == page['last_sequence']
        assert page['events'][0]['assessment'] == 'accepted'
    finally:
        await bits.coordinator.shutdown()


async def test_supervisor_checkpoint_uses_workshop_cas_and_blocks_false_completion(memory, tmp_path):
    from app.skills.collaboration import ProjectCheckpointSkill
    bits, context, room, task = await completed_artifact(memory, tmp_path)
    checkpoint = (await bits.projects.inspect(1, bits.project['id']))['workshop']
    body = {'expected_revision': checkpoint['revision'], 'objective':'Finish and verify the application',
        'acceptance':['Preserve source and inspect artifacts'], 'progress':'Implementation retained', 'status':'active'}
    try:
        await ProjectCheckpointSkill().execute(body, context)
        with pytest.raises(CoordinationError, match='changed'):
            await ProjectCheckpointSkill().execute(body, context)
        current = (await bits.projects.inspect(1, bits.project['id']))['workshop']
        with pytest.raises(CoordinationError, match='prevents mission completion'):
            await ProjectCheckpointSkill().execute({**body,'expected_revision':current['revision'],'status':'complete'}, context)
        assert (await MissionControl(bits.coordinator).snapshot(context))['checkpoint']['objective'] == body['objective']
    finally:
        await bits.coordinator.shutdown()


async def test_new_assignment_is_not_lost_while_dispatcher_is_finishing(memory, tmp_path):
    executed = []
    drained, release = asyncio.Event(), asyncio.Event()
    async def executor(spec, **kwargs):
        executed.append(spec.job_id)
        return ExecutionResult(spec.job_id, 'succeeded', 'Inspected artifact')
    bits = await project_setup(memory, tmp_path, [], executor)
    context, room = await admitted_context(memory, bits, agent='optimus')
    original = bits.projects.drain
    paused = False
    async def finishing(run_id, *, finalize=True):
        nonlocal paused
        await original(run_id, finalize=finalize)
        if not paused:
            paused = True
            drained.set()
            await release.wait()
    bits.projects.drain = finishing
    try:
        first = await bits.projects.delegate(context, Delegate(role='autobot', task='First inspection', acceptance=['Inspect only']))
        bits.projects.kick(context.run_id)
        await asyncio.wait_for(drained.wait(), 5)
        context.tool_call_id = 'additional-assignment'
        second = await bits.projects.delegate(context, Delegate(role='autobot', task='Additional inspection', acceptance=['Inspect only']))
        bits.projects.kick(context.run_id)
        release.set()
        await asyncio.wait_for(bits.projects.dispatchers[context.run_id], 5)
        assert executed == [first['task_id'], second['task_id']]
    finally:
        release.set()
        await bits.projects.release_run(context.run_id)
        await bits.coordinator.shutdown()


async def test_steering_fences_the_expected_reply_and_preserves_ack_identity(memory, tmp_path):
    from app.schemas.coordination import SupervisorSteering
    from app.models.message import Message
    async def executor(*args, **kwargs): raise AssertionError('No execution needed')
    bits = await project_setup(memory, tmp_path, [], executor)
    context, room = await admitted_context(memory, bits, agent='optimus')
    async with memory.sessions() as db, db.begin():
        step_row = await db.get(AgentStep, context.step_id)
        step_row.status = 'running'
    control = MissionControl(bits.coordinator)
    try:
        body = SupervisorSteering(input_id='fenced-owner-input',message='Keep the existing project',expected_step_id=context.step_id)
        with pytest.raises(CoordinationError) as rejected:
            await control.steer(1,context.run_id,body.model_copy(update={'expected_step_id':'0'*32}))
        assert rejected.value.code == 'steering_target_changed'
        async with memory.sessions() as db:
            assert not list(await db.scalars(select(SupervisorInput)))
        accepted = await control.steer(1,context.run_id,body)
        async with memory.sessions() as db,db.begin():
            old = await db.get(AgentStep,context.step_id)
            old.status = 'succeeded'
            db.add(AgentStep(id=uuid4().hex,run_id=context.run_id,operation_id='later-contribution',ordinal=1,
                agent_id='optimus',prompt='Later reply',status='running'))
        # Retries acknowledge the already accepted input; they never redirect it.
        assert (await control.steer(1,context.run_id,body))['id'] == accepted['id']
        with pytest.raises(CoordinationError):
            await control.steer(1,context.run_id,body.model_copy(update={'input_id':'new-input'}))
        async with memory.sessions() as db:
            originals = list(await db.scalars(select(Message).where(Message.session_id == (await db.get(Room,room['id'])).session_id)))
            assert sum('Keep the existing project' in str(message.content) for message in originals) == 1
    finally:
        await bits.coordinator.shutdown()


async def test_worker_recovery_snapshot_has_stable_parent_and_overlap_safe_cursor(memory,tmp_path):
    from app.services.worker_control import WorkerControl
    from app.services.client_compat import ClientCompatibility
    from app.models.client_compat import HiddenMessage
    async def executor(spec,**kwargs): return ExecutionResult(spec.job_id,'succeeded','Completed evidence')
    bits = await project_setup(memory,tmp_path,[delegate(),reply('The worker is running independently')],executor)
    workers=WorkerControl(bits.coordinator)
    bits.coordinator.workers=workers
    room=await bits.coordinator.create(1,CreateRoom(lead='optimus',project_id=bits.project['id']))
    try:
        result=await send(bits,room)
        async with memory.sessions() as db:
            bound=await db.get(Room,room['id'])
            task=await db.scalar(select(ProjectTask).where(ProjectTask.run_id==result['id']))
        original=workers.history_cards
        async def concurrent_activity(owner,session_id):
            await bits.coordinator.emit(result['id'],'project_event','autobot',{'task_id':task.id,
                'event':{'type':'chunk','data':'Activity during recovery snapshot' + 'x'*40000}})
            return await original(owner,session_id)
        workers.history_cards=concurrent_activity
        paid_before=len(bits.client.calls)
        snapshot=await workers.session_snapshot(1,bound.session_id)
        card=snapshot['cards'][0]
        assert card['parent_step_id']==task.parent_step and card['parent_message_id']
        assert card['run']['lastSequence'] > snapshot['sequence']
        assert card['run']['activityPartial'] is True
        replay=await workers.events(1,task.id,snapshot['sequence'])
        assert any('Activity during recovery snapshot' in str(event) for event in replay['events'])
        messages=await ClientCompatibility(bits.coordinator).messages(1,bound.session_id)
        parent=next(message for message in messages if message['id']==card['parent_message_id'])
        assert parent['coordinationStepId']==task.parent_step
        assert len(bits.client.calls)==paid_before
        async def bounded_out(owner, session_id): return {}
        workers.history_cards = bounded_out
        assert (await workers.session_snapshot(1, bound.session_id))['omitted'] == 1
        workers.history_cards = original
        async with memory.sessions() as db,db.begin():
            db.add(HiddenMessage(session_id=bound.session_id,message_id=int(card['parent_message_id']),request_id='hide-owner-action'))
        assert (await workers.session_snapshot(1,bound.session_id))['cards']==[]
        with pytest.raises(CoordinationError):await workers.session_snapshot(2,bound.session_id)
    finally:
        await bits.coordinator.shutdown()
