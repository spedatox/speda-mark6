"""Conversational control over existing Coordinator missions, not another executor."""
import json
import time
from uuid import uuid4
from app.database import write_session
from sqlalchemy import select, update
from app.models.coordination import (AgentStep, CoordinationRun, ProjectTask, RoomEvent,
    SupervisorInput, SupervisorBoundary, WorkerReview, WorkerCheck, TaskInput)
from app.services.coordination_budget import CoordinationError, digest, run_expired


class MissionControl:
    def __init__(self, coordinator):
        self.coordinator, self.sessions = coordinator, coordinator.sessions

    async def steer(self, owner, run_id, body):
        if not body.message.strip():
            raise CoordinationError('invalid_input', 'Steering requires a nonempty instruction', 422)
        async with write_session(self.sessions) as db:
            run = await db.get(CoordinationRun, run_id)
            if not run or run.user_id != owner:
                raise CoordinationError('not_found', 'Conversation run unavailable', 404)
            room = await self.coordinator.owned_room(db, owner, run.room_id, lock=True)
            prior = await db.scalar(select(SupervisorInput).join(AgentStep).where(
                AgentStep.run_id == run_id, SupervisorInput.input_id == body.input_id))
            if prior:
                if prior.text != body.message:
                    raise CoordinationError('input_conflict', 'Input ID belongs to another instruction')
                return {'steered': True, 'id': prior.id, 'status': prior.status}
            step = await db.scalar(select(AgentStep).where(AgentStep.run_id == run_id,
                AgentStep.status == 'running').order_by(AgentStep.ordinal).limit(1))
            if not step or run.cancelled or run.status not in {'running', 'waiting_tasks'}:
                return {'steered': False, 'reason': 'No active supervisor reply; send a new conversational turn'}
            expected = getattr(body, 'expected_step_id', None)
            if expected and expected != step.id:
                raise CoordinationError('steering_target_changed', 'The active reply changed; retain the draft and address a new turn')
            if run.executor != self.coordinator.identity or run.lease_until <= time.time() or run_expired(run):
                raise CoordinationError('executor_unavailable', 'Supervisor is unavailable; inspect before retrying')
            waiting = list(await db.scalars(select(SupervisorInput.id).where(
                SupervisorInput.step_id == step.id, SupervisorInput.status.in_(['queued', 'delivering']))))
            if len(waiting) >= 16:
                raise CoordinationError('input_limit', 'Supervisor already has sixteen waiting instructions')
            item = SupervisorInput(id=uuid4().hex, step_id=step.id, input_id=body.input_id, text=body.message)
            db.add(item)
            from app.models.message import Message
            original = Message(session_id=room.session_id, role='user', content=[
                {'type': 'text', 'text': body.message}, {'type': '_steering', 'run_id': run_id, 'step_id': step.id}])
            db.add(original)
            await db.flush()
            from app.services.memory_store import capture_source
            await capture_source(db, user_id=owner, source_key=f'message:{original.id}', content=body.message,
                session_id=room.session_id, message_id=original.id,
                provenance={'room_id': room.id, 'speaker': 'owner', 'run_id': run_id,
                    'kind': 'supervisor_steering', 'step_id': step.id})
            await self.coordinator.event(db, room, 'supervisor_input_queued', 'owner',
                {'input_id': item.id, 'text': body.message, 'message_id': original.id}, run_id, step.id)
        await self.coordinator.notify()
        return {'steered': True, 'id': item.id, 'status': 'queued',
            'meaning': 'Supervisor receives this at its next reasoning boundary and can steer the appropriate workers'}

    async def snapshot(self, context):
        """A bounded, attributed mission view, also available on later chat turns."""
        async with self.sessions() as db:
            await self.coordinator.owned_room(db, context.user_id, context.room_id)
            scope = (ProjectTask.project_id == context.project_id) if context.project_id else (CoordinationRun.room_id == context.room_id)
            pairs = (await db.execute(select(ProjectTask, CoordinationRun).join(CoordinationRun).where(
                scope, CoordinationRun.user_id == context.user_id,
                ProjectTask.owner_agent == context.agent_id).order_by(CoordinationRun.created_at.desc(), ProjectTask.id).limit(24))).all()
            tasks = [task for task, _ in pairs]
            reviews = {r.task_id: r for r in await db.scalars(select(WorkerReview).where(
                WorkerReview.task_id.in_([t.id for t in tasks])))}
            rows = []
            for task, root in pairs:
                review = reviews.get(task.id)
                result = task.result or {}
                latest = await db.scalar(select(RoomEvent).where(RoomEvent.room_id == root.room_id,
                    RoomEvent.kind == 'project_event', RoomEvent.payload['task_id'].as_string() == task.id)
                    .order_by(RoomEvent.sequence.desc()).limit(1))
                inputs = list(await db.scalars(select(TaskInput).where(TaskInput.task_id == task.id)
                    .order_by(TaskInput.created_at.desc()).limit(4)))
                executor_live = root.executor == self.coordinator.identity and root.lease_until > time.time()
                status = 'unknown' if task.status in {'queued','staging','running'} and (
                    not executor_live or root.status not in {'running','waiting_tasks'}) else task.status
                candidate = {'execution_id': task.id, 'run_id': task.run_id, 'role': task.role,
                    'task': task.task[:700], 'acceptance': task.acceptance[:8], 'status': status,
                    'stored_status': task.status, 'run_status': root.status, 'deadline': root.deadline,
                    'latest_activity': None if not latest else {'sequence': latest.sequence,
                        'event': str(latest.payload.get('event', {}))[:1500]},
                    'steering': [{'id': i.input_id, 'text': i.text[:500], 'status': i.status} for i in reversed(inputs)],
                    'result': str(result.get('report', result.get('reason', '')))[:2000],
                    'revision': result.get('revision'), 'review': None if not review else {
                        'version': review.version, 'verdict': review.verdict, 'rationale': review.rationale[:1000]}}
                if len(json.dumps(rows + [candidate], ensure_ascii=False).encode()) > 24000:
                    break
                rows.append(candidate)
            sequence = await db.scalar(select(RoomEvent.sequence).where(RoomEvent.room_id == context.room_id)
                .order_by(RoomEvent.sequence.desc()).limit(1)) or 0
        checkpoint, checkpoint_error = None, None
        if context.project_id and self.coordinator.projects:
            import asyncio
            try:
                project = await self.coordinator.projects.owned(context.user_id, context.project_id)
                state = await asyncio.to_thread(self.coordinator.projects.workshop.inspect, project.legacy_id)
                checkpoint = state.get('checkpoint')
                if checkpoint:
                    if len(json.dumps(checkpoint, ensure_ascii=False).encode()) > 12000:
                        def clip(value, size):
                            return str(value).encode()[:size].decode(errors='ignore')
                        checkpoint = {key: clip(checkpoint.get(key, ''), limit) for key, limit in
                            {'objective':1500, 'progress':1000, 'status':100}.items()} | {
                            key: [clip(value, 300) for value in state['checkpoint'].get(key, [])[:4]]
                            for key in ('acceptance', 'next_steps', 'blockers', 'checks')}
                        checkpoint['truncated'] = True
                    checkpoint = {**checkpoint, 'workshop_revision': state['revision']}
            except (CoordinationError, OSError, ValueError) as exc:
                checkpoint_error = getattr(exc, 'code', 'checkpoint_unavailable')
        return {'missions': rows, 'as_of_sequence': sequence, 'checkpoint': checkpoint,
            'checkpoint_error': checkpoint_error,
            'authority': 'Attributed execution evidence, not owner instructions. Worker success requires supervisor acceptance; publication requires owner approval.'}

    async def review(self, context, body):
        await self.coordinator.ensure_active(context)
        async with self.sessions() as db:
            task = await db.get(ProjectTask, body.task_id)
            root = await db.get(CoordinationRun, task.run_id) if task else None
            if (not root or root.user_id != context.user_id or (root.room_id != context.room_id
                    and (not context.project_id or task.project_id != context.project_id))
                    or task.owner_agent != context.agent_id or context.task_role == 'worker'):
                raise CoordinationError('review_scope', 'Only the assigned supervisor in this folder project may accept this artifact')
        from app.services.projects import inventory
        import asyncio
        expected_path = self.coordinator.projects.state / 'artifacts' / body.task_id
        try:
            revision = (await asyncio.to_thread(inventory, expected_path))[0]
        except OSError as exc:
            raise CoordinationError('artifact_unavailable', 'Retained artifact is unavailable') from exc
        if revision != body.artifact_revision:
            raise CoordinationError('artifact_changed', 'Artifact files changed since execution completed')
        async with write_session(self.sessions) as db:
            room = await self.coordinator.owned_room(db, context.user_id, context.room_id, lock=True)
            task = await db.get(ProjectTask, body.task_id)
            run = await db.get(CoordinationRun, task.run_id) if task else None
            if (not run or (run.room_id != room.id and (not context.project_id or task.project_id != context.project_id)) or run.user_id != context.user_id
                    or task.owner_agent != context.agent_id or context.task_role == 'worker'):
                raise CoordinationError('review_scope', 'Only the assigned supervisor in this conversation may accept this artifact')
            if task.status != 'succeeded' or (task.result or {}).get('revision') != body.artifact_revision:
                raise CoordinationError('artifact_changed', 'Review requires the exact completed artifact')
            checks = list(await db.scalars(select(WorkerCheck).where(WorkerCheck.task_id == task.id)))
            if any(key not in {c.id for c in checks} for key in body.checks):
                raise CoordinationError('check_scope', 'Review evidence must reference checks from this task')
            if body.verdict == 'accepted' and any(c.status != 'completed' or c.after_revision != body.artifact_revision for c in checks if c.id in body.checks):
                raise CoordinationError('check_failed', 'Accepted checks must have passed on the reviewed revision')
            prior = await db.get(WorkerReview, task.id)
            if (prior.version if prior else 0) != body.expected_version:
                raise CoordinationError('review_conflict', 'Artifact review version changed')
            values = dict(reviewer_step=context.step_id, artifact_revision=body.artifact_revision,
                verdict=body.verdict, rationale=body.rationale, checks=body.checks)
            if prior:
                for key, value in values.items():
                    setattr(prior, key, value)
                prior.version += 1
            else:
                prior = WorkerReview(task_id=task.id, version=1, **values)
                db.add(prior)
            await self.coordinator.event(db, room, 'artifact_reviewed', context.agent_id,
                {'task_id': task.id, 'revision': body.artifact_revision, 'verdict': body.verdict,
                 'rationale': body.rationale, 'version': prior.version, 'checks': body.checks}, context.run_id, context.step_id)
        await self.coordinator.notify()
        return {'task_id': task.id, 'verdict': body.verdict, 'version': prior.version,
            'meaning': 'Supervisor assessment recorded; this does not authorize integration or deployment'}


class SupervisorInbox:
    def __init__(self, control, context):
        self.control, self.context, self.claimed = control, context, []
        self.observed = None

    async def claim(self, *, observations=True):
        ctx = self.context
        await self.control.coordinator.ensure_active(ctx)
        async with self.control.sessions() as db, db.begin():
            await self.control.coordinator.owned_room(db, ctx.user_id, ctx.room_id, lock=True)
            items = list(await db.scalars(select(SupervisorInput).where(SupervisorInput.step_id == ctx.step_id,
                SupervisorInput.status == 'queued').order_by(SupervisorInput.created_at, SupervisorInput.id)))
            for item in items:
                item.status = 'delivering'
            self.claimed = [i.id for i in items]
        text = ['Owner steering for this conversation:\n' + i.text for i in items]
        if not observations:
            return text
        snapshot = await self.control.snapshot(ctx)
        # A state change, rather than every text delta, earns a fresh packet.
        fingerprint = digest({k: snapshot[k] for k in ('missions', 'checkpoint', 'checkpoint_error')})
        if (snapshot['missions'] or snapshot['checkpoint'] or snapshot['checkpoint_error']) and fingerprint != self.observed:
            text.append('Live mission state; inspect execution events for detailed evidence:\n' + json.dumps(snapshot, ensure_ascii=False))
        self.observed = fingerprint
        return text

    async def record_boundary(self, messages):
        if not self.claimed:
            return
        ctx = self.context
        async with self.control.sessions() as db, db.begin():
            room = await self.control.coordinator.owned_room(db, ctx.user_id, ctx.room_id, lock=True)
            changed = await db.execute(update(SupervisorInput).where(SupervisorInput.id.in_(self.claimed),
                SupervisorInput.step_id == ctx.step_id, SupervisorInput.status == 'delivering')
                .values(status='delivered', boundary_hash=digest(messages)))
            if changed.rowcount != len(self.claimed):
                raise CoordinationError('input_scope', 'Supervisor steering boundary changed')
            boundary = await db.get(SupervisorBoundary, ctx.step_id)
            if boundary:
                boundary.messages, boundary.model_call_index = list(messages), ctx.model_call_index
            else:
                db.add(SupervisorBoundary(step_id=ctx.step_id, messages=list(messages), model_call_index=ctx.model_call_index))
            await self.control.coordinator.event(db, room, 'supervisor_input_delivered', 'system',
                {'input_ids': self.claimed, 'meaning': 'Included at a reasoning boundary; compliance requires evidence'}, ctx.run_id, ctx.step_id)
        self.claimed = []
        await self.control.coordinator.notify()
