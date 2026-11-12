"""Stable project bindings, Workshop checkpoints and supervised isolated snapshots.

The canonical project is never modified by a worker. Results are retained for an
explicit integration step. Host configuration is trusted; a model cannot choose
a mount, enable networking or retarget a room.
"""
import asyncio
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
from uuid import NAMESPACE_URL, uuid4, uuid5
from app.database import write_session
from sqlalchemy import select, update, func, case
from sqlalchemy.exc import IntegrityError
from app.core.context import AgentContext
from app.models.coordination import (Project, ProjectTask, CoordinationRun, Room, CellReceipt,
    AgentStep, TaskInput, WorkerCheck, ForgeBoundary, ProjectOperation, WorkerReview)
from app.models.model_policy import ToolExecutionReceipt
from app.models.legion import LegionExecution
from app.schemas.llm import PolicyDocument, ThinkingRequest
from app.services.coordination_budget import CoordinationError
from app.services.forge_adapter import ForgeEffects, ForgeModel
from forge.config import ForgeSettings
from forge.runtime import ExecutionSpec, execute
from forge.workshop import Workshop


IGNORED = {".git", ".forge", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache"}


def inventory(root):
    """Bounded content manifest; never follow links/junctions out of a project."""
    root = Path(root).resolve(strict=True)
    rows, total = [], 0
    for folder, directories, files in os.walk(root, followlinks=False):
        directories[:] = sorted(d for d in directories if d not in IGNORED)
        for name in [*directories, *files]:
            path = Path(folder) / name
            if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                raise CoordinationError("linked_workspace", "Linked project entries need an explicit mount policy")
        for name in sorted(files):
            if name == ".env" or name.startswith(".env.") and name != ".env.example":
                continue
            path = Path(folder) / name
            total += path.stat().st_size
            if total > 100 * 1024 * 1024 or len(rows) >= 20000:
                raise CoordinationError("snapshot_limit", "Project exceeds the configured snapshot bounds")
            if not path.resolve().is_relative_to(root):
                raise CoordinationError("workspace_escape", "Project path left its authorized root")
            with path.open('rb') as source:
                digest = hashlib.file_digest(source, 'sha256').hexdigest()
            rows.append((path.relative_to(root).as_posix(), digest, path.stat().st_mode & 0o777))
    rows.sort()
    revision = hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()
    return revision, rows


def snapshot(source, target):
    before, rows = inventory(source)
    target.mkdir(parents=True, exist_ok=False)
    for name, _, _ in rows:
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(source) / name, destination)
    copied, _ = inventory(target)
    after, _ = inventory(source)
    if copied != before or after != before:
        raise CoordinationError("snapshot_changed", "Project changed during staging; retained snapshot needs inspection")
    return before


def worker_metadata(task):
    """Server-assigned worker attribution; team leadership is not a forged turn."""
    from app.profiles.workers import worker_spec
    spec = worker_spec(task.role)
    return {"task_id": task.id, "role": task.role, "worker_id": spec.worker_id,
        "role_label": spec.worker_id.replace('_', ' ').title(), "backend": spec.backend,
        "team": spec.team, "team_lead": spec.team_lead, "runtime_role": spec.runtime_role,
        "read_only": spec.read_only, "owner_agent": task.owner_agent,
        "supervisor_agent": task.owner_agent, "parent_step": task.parent_step,
        "parent_step_id": task.parent_step}


def cell_image_for(settings, role):
    try: images=json.loads(settings.project_cell_images)
    except (ValueError,TypeError): raise CoordinationError('cell_image','Configure a valid trusted per-role image map') from None
    if (not isinstance(images,dict) or set(images)-{'coder','reviewer','pentester'} or
            any(not isinstance(value,str) or not value.strip() or len(value)>255 or any(c.isspace() for c in value) for value in images.values())):
        raise CoordinationError('cell_image','Only trusted coder/reviewer/pentester image references are accepted')
    return images.get(role) or settings.project_cell_image or None


class Projects:
    def __init__(self, sessions, coordinator, settings, *, executor=execute):
        self.sessions, self.coordinator, self.settings = sessions, coordinator, settings
        self.enabled, self.executor = settings.project_execution_enabled, executor
        self.jobs, self.signals = {}, {}
        self.dispatchers, self.drain_locks = {}, {}
        self.dispatch_pending = set()
        self.project_locks = {}
        from app.services.cloud_projects import CloudEnvironments
        self.cloud = CloudEnvironments(self)
        self.root = Path(settings.project_workspace_root).resolve() if settings.project_workspace_root else None
        self.state = Path(settings.project_state_root).resolve() if settings.project_state_root else None
        self.workshop = None
        if self.root and self.state:
            if self.state == self.root or self.state.is_relative_to(self.root) or self.root.is_relative_to(self.state):
                raise ValueError("Project state and workspace roots must be disjoint")
            self.workshop = Workshop(self.state)

    def configured(self):
        if self.workshop is None:
            raise CoordinationError("project_unconfigured", "Configure the trusted project workspace and state roots", 503)

    def path(self, value):
        self.configured()
        try:
            path = Path(value).resolve(strict=True)
        except OSError as exc:
            raise CoordinationError("project_missing", "Project directory is unavailable on the configured host", 404) from exc
        if not path.is_dir() or path == self.root or not path.is_relative_to(self.root):
            raise CoordinationError("project_scope", "Select an individual project under the configured workspace root", 422)
        if any(part in IGNORED for part in path.relative_to(self.root).parts):
            raise CoordinationError('project_scope','Repository and execution metadata cannot be selected as a project',422)
        return path

    async def owned(self, owner, project_id, *, allow_standalone=False):
        self.configured()
        async with self.sessions() as db:
            project = await db.get(Project, project_id)
            if project is None or project.user_id != owner:
                raise CoordinationError("not_found", "Project unavailable", 404)
            if project.host_id != self.settings.project_host_id:
                raise CoordinationError("host_mismatch", "This executor is not the project's registered host")
            if allow_standalone and Path(project.path) == self.state / "standalone" / project.id:
                path = Path(project.path)
                if not path.is_dir() or path.resolve() != path:
                    raise CoordinationError("project_missing", "Standalone working folder is unavailable", 404)
            else:
                self.path(project.path)
            return project

    async def _standalone_project(self, db, room):
        """Back a standalone chat with a private, empty Workshop project."""
        self.configured()
        project_id = uuid5(NAMESPACE_URL, f"speda:standalone:{room.user_id}:{room.id}").hex
        project = await db.get(Project, project_id)
        if project is None:
            path = self.state / "standalone" / project_id
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            if path.resolve() != path:
                raise CoordinationError("project_scope", "Standalone working folder left its execution root")
            registered = await asyncio.to_thread(self.workshop.register, path)
            project = Project(id=project_id, user_id=room.user_id, name="Standalone Forge work",
                host_id=self.settings.project_host_id, path=str(path), legacy_id=registered["id"])
            db.add(project)
        return project

    async def create(self, owner, body):
        path = self.path(body.path)
        registered = await asyncio.to_thread(self.workshop.register, path)
        match = (Project.path == str(path)) | ((Project.legacy_id == registered['id'])
            & (Project.host_id == self.settings.project_host_id))
        try:
            async with write_session(self.sessions) as db:
                existing = await db.scalar(select(Project).where(match))
                if existing:
                    if existing.user_id != owner:
                        raise CoordinationError("claimed_project", "Project belongs to another owner")
                    project_id = existing.id
                else:
                    project_id = uuid4().hex
                    db.add(Project(id=project_id, user_id=owner, name=body.name,
                        host_id=self.settings.project_host_id, path=str(path), legacy_id=registered["id"]))
        except IntegrityError:
            async with self.sessions() as db:
                existing = await db.scalar(select(Project).where(match))
                if existing is None:
                    raise
                if existing.user_id != owner:
                    raise CoordinationError('claimed_project', 'Project belongs to another owner') from None
                project_id = existing.id
        return await self.inspect(owner, project_id)

    async def inspect(self, owner, project_id, *, allow_standalone=False):
        project = await self.owned(owner, project_id, allow_standalone=allow_standalone)
        state = await asyncio.to_thread(self.workshop.inspect, project.legacy_id)
        async with self.sessions() as db:
            tasks = (await db.execute(select(ProjectTask).where(ProjectTask.project_id == project_id)
                .order_by(ProjectTask.id).limit(100))).scalars().all()
            cells = (await db.execute(select(CellReceipt).where(CellReceipt.task_id.in_([t.id for t in tasks])))).scalars().all()
            inputs = (await db.execute(select(TaskInput).where(TaskInput.task_id.in_([t.id for t in tasks])))).scalars().all()
            checks = (await db.execute(select(WorkerCheck).where(WorkerCheck.task_id.in_([t.id for t in tasks])))).scalars().all()
            boundaries = list(await db.scalars(select(ForgeBoundary).where(ForgeBoundary.task_id.in_([t.id for t in tasks]))))
            releases = list(await db.scalars(select(ProjectOperation).where(ProjectOperation.project_id == project_id,
                ProjectOperation.kind == 'deploy').order_by(ProjectOperation.id).limit(50)))
        return {"id": project.id, "name": project.name, "host_id": project.host_id, "path": project.path,
            "location_version": project.location_version, "workshop": state,
            "execution_checkpoints": [{"task_id": b.task_id, "boundary_hash": b.boundary_hash,
                "artifact_revision": b.artifact_revision, "model_call_index": b.model_call_index,
                "updated_at": b.updated_at.isoformat() + 'Z', "recovery": "Requires verified Cell closure and receipt reconciliation; never auto-replayed"} for b in boundaries],
            "releases": [{"id": r.id, "status": r.status, "plan_hash": r.details['plan_hash'],
                "target": r.details['plan']['target'], "artifact_revision": r.details['plan']['artifact_revision']} for r in releases],
            "inputs": [{"id": i.id, "task_id": i.task_id, "input_id": i.input_id, "message": i.text,
                "status": i.status, "boundary_hash": i.boundary_hash} for i in inputs],
            "checks": [{"id": c.id, "task_id": c.task_id, "call_id": c.call_id, "command": c.command,
                "status": c.status, "before_revision": c.before_revision,
                "after_revision": c.after_revision, "result": c.result} for c in checks],
            "cells": [{"task_id": c.task_id, "status": c.status, "backend": c.backend,
                "handle": c.handle, "workspace": c.workspace} for c in cells],
            "tasks": [{"id": t.id, "run_id": t.run_id, **worker_metadata(t),
                "task": t.task, "acceptance": t.acceptance, "status": t.status,
                "cancel_requested": t.cancelled, "dependencies": t.dependencies,
                "workspace": t.workspace, "base_revision": t.base_revision, "result": t.result} for t in tasks]}

    async def checkpoint_blockers(self, owner, project_id):
        await self.owned(owner, project_id)
        async with self.sessions() as db:
            tasks = (await db.execute(select(ProjectTask, WorkerReview).outerjoin(
                WorkerReview, WorkerReview.task_id == ProjectTask.id
            ).where(ProjectTask.project_id == project_id).order_by(ProjectTask.id))).all()

        blockers = []
        for task, review in tasks:
            if task.status != 'succeeded':
                reason = f'task status is {task.status}'
            elif review is None:
                reason = 'artifact review is missing'
            elif review.artifact_revision != (task.result or {}).get('revision'):
                reason = 'review is for an older or different artifact revision'
            elif review.verdict != 'accepted':
                reason = f'artifact review verdict is {review.verdict}'
            else:
                continue
            blockers.append(f'Task {task.id}: {reason}.')
        return blockers

    async def checkpoint(self, owner, project_id, body):
        project = await self.owned(owner, project_id)
        try:
            return await asyncio.to_thread(self.workshop.checkpoint, Path(project.path),
                revision=body.expected_revision, state=body.model_dump(exclude={"expected_revision"}))
        except ValueError as exc:
            raise CoordinationError("checkpoint_conflict", str(exc)) from exc

    async def delegate(self, context, body):
        if not self.enabled:
            raise CoordinationError("workers_disabled", "Project execution is disabled")
        if context.task_role == "worker":
            raise CoordinationError("worker_depth", "Workers cannot start further workers")
        await self.coordinator.ensure_active(context)
        project = await self.owned(context.user_id, context.project_id) if context.project_id else None
        operation = f"{context.step_id}:{context.tool_call_id}"
        async with write_session(self.sessions) as db:
            room = await self.coordinator.owned_room(db, context.user_id, context.room_id, lock=True)
            run = await db.get(CoordinationRun, context.run_id)
            if run.room_id != room.id or room.project_id != context.project_id:
                raise CoordinationError("project_binding", "Worker assignment must use this run's immutable room/project binding")
            roles = run.profiles.get(context.agent_id, {}).get("worker_roles", [])
            if body.role not in roles:
                raise CoordinationError("worker_authority", "This persona cannot assign that worker role")
            if project is None:
                project = await self._standalone_project(db, room)
            rows = list((await db.execute(select(ProjectTask).where(ProjectTask.run_id == context.run_id))).scalars())
            for task in rows:
                if task.operation_id == operation:
                    if (task.role != body.role or task.task != body.task or task.acceptance != body.acceptance
                            or task.dependencies != body.dependencies or task.project_id != project.id
                            or task.owner_agent != context.agent_id):
                        raise CoordinationError("task_conflict", "Worker operation ID belongs to another assignment")
                    return {"task_id": task.id, "status": task.status}
            legion_count=await db.scalar(select(func.count()).select_from(LegionExecution).where(LegionExecution.run_id == context.run_id))
            if len(rows) + legion_count >= 8:
                raise CoordinationError("task_limit", "This run has reached its eight-task queue limit")
            if body.dependencies:
                eligible = set(await db.scalars(select(ProjectTask.id).join(CoordinationRun).where(
                    CoordinationRun.user_id == context.user_id,
                    (CoordinationRun.room_id == context.room_id) | (ProjectTask.owner_agent == context.agent_id),
                    ProjectTask.project_id == project.id)))
                if any(d not in eligible for d in body.dependencies):
                    raise CoordinationError("dependency_scope", "Dependencies must belong to this supervisor and execution project")
            task = ProjectTask(id=uuid4().hex, project_id=project.id, run_id=context.run_id,
                operation_id=operation, parent_step=context.step_id, owner_agent=context.agent_id,
                role=body.role, task=body.task, acceptance=body.acceptance, dependencies=body.dependencies)
            db.add(task)
            await db.flush()
            await self.coordinator.event(db, room, "project_queued", context.agent_id,
                {**worker_metadata(task), "task": task.task, "acceptance": task.acceptance,
                 "dependencies": task.dependencies, "project_id": project.id}, context.run_id, context.step_id)
        return {"task_id": task.id, "status": "queued"}

    def kick(self, run_id):
        """Dispatch after the supervisor tool receipt commits; keep the chat alive."""
        self.dispatch_pending.add(run_id)
        current = self.dispatchers.get(run_id)
        if current is None or current.done():
            async def dispatch():
                while run_id in self.dispatch_pending:
                    self.dispatch_pending.discard(run_id)
                    await self.drain(run_id, finalize=False)
            job = asyncio.create_task(dispatch())
            self.dispatchers[run_id] = job
            def finished(done):
                # Consume failures; Coordinator's final drain still reconciles state.
                if not done.cancelled():
                    done.exception()
            job.add_done_callback(finished)

    async def release_run(self, run_id):
        dispatcher = self.dispatchers.pop(run_id, None)
        if dispatcher:
            await asyncio.gather(dispatcher, return_exceptions=True)
        self.dispatch_pending.discard(run_id)
        self.drain_locks.pop(run_id, None)

    async def drain(self, run_id, *, finalize=True):
        lock = self.drain_locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            if finalize and self.settings.project_supervision_enabled:
                while True:
                    await self._drain(run_id, finalize=False)
                    if not await self.supervise(run_id):
                        break
                    if self.coordinator.workers:
                        await self.coordinator.workers.wait_run(run_id)
            await self._drain(run_id, finalize=finalize)

    async def supervise(self, run_id):
        """A bounded IGOR acceptance turn under the original root authority."""
        from app.models.coordination import WorkerReview
        from app.models.user import User
        from app.models.message import Message
        from app.schemas.coordination import SpendingPolicy
        async with write_session(self.sessions) as db:
            run = await db.get(CoordinationRun, run_id)
            room = await self.coordinator.owned_room(db, run.user_id, run.room_id, lock=True)
            if not room.project_id or run.cancelled or run.deadline <= time.time():
                return False
            # Detached workers may finish before the public conversation settles.
            # Let its queued/running contributions finish before admitting an
            # acceptance turn; Coordinator's final drain comes back here.
            speaking = await db.scalar(select(AgentStep.id).where(AgentStep.run_id == run_id,
                AgentStep.status.in_(['queued', 'running', 'waiting_reply', 'cancel_requested'])).limit(1))
            if speaking:
                return False
            tasks = list(await db.scalars(select(ProjectTask).where(ProjectTask.run_id == run_id,
                ProjectTask.project_id == room.project_id, ProjectTask.status == 'succeeded')))
            existing = set(await db.scalars(select(AgentStep.operation_id).where(AgentStep.run_id == run_id)))
            reviewed = set(await db.scalars(select(WorkerReview.task_id).where(WorkerReview.task_id.in_([t.id for t in tasks]))))
            task = next((t for t in tasks if 'forge-review:' + t.id not in existing and t.id not in reviewed), None)
            if task is None:
                return False
            count = await db.scalar(select(func.count()).select_from(AgentStep).where(AgentStep.run_id == run_id))
            if count >= SpendingPolicy.model_validate(run.limits).run.contributions:
                await self.coordinator.event(db, room, 'supervision_deferred', task.owner_agent,
                    {'task_id': task.id, 'reason': 'Original contribution allowance exhausted; acceptance remains pending'}, run_id)
                return False
            step = AgentStep(id=uuid4().hex, run_id=run_id, operation_id='forge-review:' + task.id,
                parent_id=task.parent_step, ordinal=count, agent_id=task.owner_agent, mode='mission_review',
                status='running', prompt='Evaluate completed FORGE assignment ' + task.id + '. '
                    'Inspect the exact artifact, acceptance criteria, actual checks and any steering receipts. '
                    'Use project_review to accept, request changes or block it with evidence. '
                    'You may delegate scoped corrections or exact-artifact review within the original authority and budget. '
                    'Do not integrate or deploy without the owner approval path. A worker report alone proves no check. '
                    'Provide a concise progress update with remaining work.')
            db.add(step)
            user = await db.get(User, run.user_id)
            await self.coordinator.event(db, room, 'speaker_started', step.agent_id,
                {'mode': 'mission_review', 'task_id': task.id, 'profile_revision': run.profiles[step.agent_id]['revision']}, run_id, step.id)
        await self.coordinator.notify()
        context = await self.coordinator.context_for(run, step, room, user)
        job = asyncio.create_task(self.coordinator._speak(run_id, step, context))
        self.coordinator.replies[step.id] = job
        try:
            text = await job
            async with write_session(self.sessions) as db:
                current = await db.get(AgentStep, step.id)
                room = await self.coordinator.owned_room(db, run.user_id, run.room_id, lock=True)
                await self.coordinator.ensure_active(context)
                current.status, current.result = 'succeeded', text
                message = Message(session_id=room.session_id, role='assistant', content=[
                    {'type': 'text', 'text': text}, {'type': '_speaker', 'agent_id': step.agent_id,
                     'room_id': room.id, 'step_id': step.id}])
                db.add(message)
                await db.flush()
                await self.coordinator.event(db, room, 'message', step.agent_id,
                    {'text': text, 'message_id': message.id, 'mode': 'mission_review'}, run_id, step.id)
                await db.execute(update(CoordinationRun).where(CoordinationRun.id == run_id)
                    .values(contributions=CoordinationRun.contributions + 1))
                await self.coordinator.persist_memory(db, context)
        except BaseException as exc:
            async with write_session(self.sessions) as db:
                current = await db.get(AgentStep, step.id)
                stopped = current.status == 'cancel_requested' and not asyncio.current_task().cancelling()
                current.status = 'cancelled' if stopped else 'reconciling'
                current.result = 'Supervisor contribution stopped; acceptance remains pending' if stopped else type(exc).__name__
            if stopped:
                await self.coordinator.emit(run_id, 'contribution_stopped', step.agent_id,
                    {'reason': 'Supervisor acceptance stopped; artifact retained'}, step.id)
                return True
            raise
        finally:
            self.coordinator.replies.pop(step.id, None)
        await self.coordinator.notify()
        return True

    async def _drain(self, run_id, *, finalize):
        # A serial dependency executor is intentional: one writer per project.
        # Independent model calls still share the root concurrency gate.
        while True:
            waiting = False
            async with write_session(self.sessions) as db:
                run = await db.get(CoordinationRun, run_id)
                tasks = list((await db.execute(select(ProjectTask).where(ProjectTask.run_id == run_id))).scalars())
                pending = [t for t in tasks if t.status == "queued"]
                if not pending or run.cancelled:
                    break
                dependencies = {d for t in pending for d in t.dependencies}
                predecessors = list(await db.scalars(select(ProjectTask).where(ProjectTask.id.in_(dependencies))))
                states = {t.id: t.status for t in [*tasks, *predecessors]}
                ready = next((t for t in pending if all(states[d] == "succeeded" for d in t.dependencies)), None)
                if ready is None:
                    for t in pending:
                        if any(states.get(d) not in {'queued', 'staging', 'running', 'succeeded'} for d in t.dependencies):
                            t.status = "blocked"
                            t.result = {"reason": "Required predecessor did not succeed"}
                        else:
                            waiting = True
                    if not waiting:
                        break
                    if run.deadline <= time.time():
                        raise CoordinationError('deadline', 'Dependency wait reached the original run deadline')
                # CAS also fences concurrent processes; Workshop fences projects.
                if ready:
                    claimed = await db.execute(update(ProjectTask).where(ProjectTask.id == ready.id,
                        ProjectTask.status == 'queued').values(status='staging'))
                    if not claimed.rowcount:
                        continue
                    task_id = ready.id
            if waiting:
                try:
                    async with self.coordinator._changed:
                        await asyncio.wait_for(self.coordinator._changed.wait(), .5)
                except TimeoutError:
                    pass
                continue
            job = asyncio.create_task(self.run_task(task_id))
            self.jobs[task_id] = job
            try:
                await job
            finally:
                self.jobs.pop(task_id, None)
        if not finalize:
            return
        async with write_session(self.sessions) as db:
            run = await db.get(CoordinationRun, run_id)
            tasks = list((await db.execute(select(ProjectTask).where(ProjectTask.run_id == run_id))).scalars())
            statuses = {t.status for t in tasks}
            uncertain = await self.coordinator.unresolved_outcomes(db, run_id)
            stopped = await db.scalar(select(func.count()).select_from(AgentStep).where(
                AgentStep.run_id == run_id, AgentStep.status == "cancelled"))
            incomplete_children = await db.scalar(select(func.count()).select_from(LegionExecution).where(
                LegionExecution.run_id == run_id, LegionExecution.status != 'succeeded'))
            run.status = ("reconciling" if uncertain or statuses & {"reconciling", "running", "staging"} else
                          "cancelled" if run.cancelled else "failed" if "failed" in statuses else
                          "partial" if stopped or incomplete_children or "cancelled" in statuses else
                          "succeeded" if statuses == {"succeeded"} else "failed")
        await self.coordinator.emit(run_id, "project_run_finished", "system", {"status": run.status,
            "tasks": [{"id": t.id, **worker_metadata(t), "status": t.status,
                       "reason": (t.result or {}).get("reason")} for t in tasks]})

    async def run_task(self, task_id):
        async with self.sessions() as db:
            task = await db.get(ProjectTask, task_id)
            run = await db.get(CoordinationRun, task.run_id)
        if run.policy.get('cloud_environment'):
            # Every task gets its own workspace and Cell claim. The published
            # baseline is read-only and can seed several independent runs.
            await self._run_task(task_id)
            return
        lock = self.project_locks.setdefault(task.project_id, asyncio.Lock())
        signal = self.signals.setdefault(task_id, asyncio.Event())
        # Multiple conversational rounds may assign work to the same project.
        # Wait cooperatively instead of failing a healthy Workshop claim.
        acquired = False
        try:
            while not acquired and not signal.is_set():
                try:
                    await asyncio.wait_for(lock.acquire(), .25)
                    acquired = True
                except TimeoutError:
                    async with self.sessions() as db:
                        run = await db.get(CoordinationRun, task.run_id)
                    if run.deadline <= time.time() or run.cancelled:
                        signal.set()
            if not acquired:
                async with write_session(self.sessions) as db:
                    await db.execute(update(ProjectTask).where(ProjectTask.id == task_id).values(status='cancelled'))
                return
            await self._run_task(task_id)
        finally:
            if acquired:
                lock.release()
            self.signals.pop(task_id, None)

    async def _run_task(self, task_id):
        from app.services.damage_control import exception_facts

        signal = self.signals.setdefault(task_id, asyncio.Event())
        source_claim = f"source-{task_id}"
        claimed, returned, task = False, False, None
        protocol = getattr(getattr(self.coordinator, "orchestrator", None), "incidents", None)
        incident = None
        try:
            async with self.sessions() as db:
                task = await db.get(ProjectTask, task_id)
                run = await db.get(CoordinationRun, task.run_id)
                room = await db.get(Room, run.room_id)
                dependencies = [await db.get(ProjectTask, d) for d in task.dependencies]
            if task.cancelled or run.cancelled:
                async with write_session(self.sessions) as db:
                    await db.execute(update(ProjectTask).where(ProjectTask.id == task_id).values(status="cancelled"))
                return
            project = await self.owned(run.user_id, task.project_id, allow_standalone=True)
            if self.settings.project_cell_backend != "docker":
                raise CoordinationError("cell_backend", "Project workers require the configured Docker isolation backend")
            environment = run.policy.get('cloud_environment')
            if environment:
                predecessors = [(d.result or {}).get('cloud_environment') for d in dependencies]
                if predecessors:
                    first_environment = predecessors[0]
                    if not first_environment or any(item != first_environment for item in predecessors):
                        raise CoordinationError('cloud_dependency_scope', 'Follow-up tasks must share one published environment')
                    environment = first_environment
                source = await self.cloud.source(environment)
                handoff = await asyncio.to_thread(self.workshop.inspect, project.legacy_id)
            else:
                handoff = await asyncio.to_thread(self.workshop.claim, Path(project.path), source_claim, task.task)
                claimed = True
                source = Path(project.path)
            canonical_revision = (await asyncio.to_thread(inventory, source))[0]
            # A review/change depending on one result receives that exact artifact.
            # Multiple divergent artifacts need explicit integration, not guessed merges.
            artifacts = {d.result.get("workspace") for d in dependencies if d.result and d.result.get("workspace")}
            previous = None
            if len(artifacts) > 1:
                raise CoordinationError("integration_required", "Multiple artifact branches require an integration step")
            if artifacts:
                if any(canonical_revision not in {d.result.get('revision'),
                        d.result.get('canonical_source_revision', d.base_revision)} for d in dependencies):
                    raise CoordinationError('dependency_source_changed',
                        'Canonical folder changed since the predecessor; reconcile current source before continuing its artifact')
                source = Path(next(iter(artifacts)))
                actual, _ = await asyncio.to_thread(inventory, source)
                if any(d.result.get("revision") != actual for d in dependencies):
                    raise CoordinationError("stale_artifact", "A dependency artifact changed after verification")
            elif environment and environment.get('target') and not dependencies:
                previous = run.policy.get('cloud_checkpoints', {}).get(task.owner_agent)
                if previous:
                    source = self.state / 'artifacts' / previous['task_id']
                    if (await asyncio.to_thread(inventory, source))[0] != previous['artifact_revision']:
                        raise CoordinationError('stale_artifact', 'The published checkpoint artifact changed after verification')
            target = self.state / "artifacts" / task.id
            revision = await asyncio.to_thread(snapshot, source, target)
            policy_data = run.policy
            deadline = time.monotonic() + max(0, run.deadline - time.time())
            parent_policy = await self.coordinator.policy.resolve(model=policy_data.get("model"),
                user_id=run.user_id, agent_id=task.owner_agent,
                request=ThinkingRequest.model_validate(policy_data["thinking"]) if policy_data.get("thinking") else None,
                snapshot=(policy_data["revision"], PolicyDocument.model_validate(policy_data["document"])),
                deadline=deadline)
            effective = await self.coordinator.policy.resolve_worker_policy(task.role, model=policy_data.get("model"),
                parent=parent_policy, user_id=run.user_id, agent_id=task.owner_agent,
                snapshot=(policy_data["revision"], PolicyDocument.model_validate(policy_data["document"])),
                deadline=deadline)
            ctx = AgentContext(user_id=run.user_id, session_id=room.session_id, request_id=task.id,
                triggered_by="agent", trigger_payload={}, output_mode="silent", model=effective.spec.ref,
                system_prompt="", conversation_history=[], db=None, timezone="UTC", agent_id=task.owner_agent,
                task_role="worker", effective_policy=replace(effective, fallbacks=()), room_id=room.id,
                run_id=run.id, step_id=task.id, project_id=project.id, coordination=self.coordinator,
                budget_handle=self.coordinator.budget, output_token_cap=policy_data["output_tokens"])
            if protocol:
                ctx.extra["incident_worker_role"] = task.role
                incident = protocol.begin(ctx)
            async with write_session(self.sessions) as db:
                await db.execute(update(ProjectTask).where(ProjectTask.id == task.id)
                    .values(status="running", workspace=str(target), base_revision=revision))
            await self.coordinator.ensure_active(ctx)
            await self.coordinator.emit(run.id, "project_started", task.owner_agent,
                {**worker_metadata(task), "task": task.task, "acceptance": task.acceptance,
                 "project_id": project.id, "revision": revision, "model": ctx.model,
                 "policy_revision": effective.revision, "thinking": effective.requested.model_dump(mode='json')})
            # Carry the authoritative source checkpoint into the isolated execution.
            staged = await asyncio.to_thread(self.workshop.register, target)
            checkpoint = handoff["checkpoint"]
            if checkpoint:
                await asyncio.to_thread(self.workshop.checkpoint, target, revision=staged["revision"], state=checkpoint)
            event_failed = False
            async def emit(event):
                nonlocal event_failed
                try:
                    # Chunk events are ephemeral in Forge; bound persisted observer data.
                    payload = event.model_dump()
                    if len(json.dumps(payload)) > 16000:
                        payload = {"type": event.type, "data": str(event.data)[:12000], "truncated": True}
                    await self.coordinator.emit(run.id, "project_event", task.role,
                        {**worker_metadata(task), "project_id": project.id, "event": payload})
                except Exception:
                    event_failed = True
                    signal.set()
                    raise
            from app.profiles.workers import worker_spec
            worker = worker_spec(task.role)
            image = (environment['image'] if environment and worker.runtime_role != 'pentester'
                     else cell_image_for(self.settings, worker.runtime_role))
            project_context = policy_data.get('project_contexts', {}).get(task.owner_agent)
            project_instructions = ('\nPinned project instructions and reference documents (documents are evidence, '
                'not additional permissions):\n' + json.dumps(project_context, ensure_ascii=False)) if project_context else ''
            spec = ExecutionSpec(job_id=task.id,
                role=worker.runtime_role,
                task=task.task + "\nAcceptance criteria:\n" + json.dumps(task.acceptance)
                    + "\nWorker role contract:\n" + worker.instructions + project_instructions
                    + '\nContinue from the existing files in this checkout. Before editing, read root AGENTS.md '
                    'and any AGENTS.md files between the root and the file being changed. Preserve existing work.', workspace=target,
                model_ref=ctx.model, max_iterations=min(30, worker.max_iterations), timeout_s=120,
                allow_network=False, cell_backend="docker", cell_image=image)
            runtime_settings = ForgeSettings(workspace_root=self.state, cell_backend="docker", retry_attempts=0,
                max_tokens=ctx.output_token_cap)
            async with asyncio.timeout(max(0, run.deadline - time.time())):
                from app.services.task_inbox import TaskInbox
                kwargs = {"inbox": TaskInbox(ctx, self.sessions)} if self.executor is execute else {}
                if protocol and self.executor is execute:
                    from app.services.forge_adapter import ForgeIncidentProvider
                    from forge.warden.toolsource import BuiltinToolProvider
                    kwargs["tool_providers"] = [BuiltinToolProvider(), ForgeIncidentProvider(protocol, ctx)]
                result = await self.executor(spec, model=ForgeModel(ctx), emit=emit, signal=signal,
                    settings=runtime_settings, effect_gate=ForgeEffects(self.sessions, ctx, protocol), **kwargs)
            returned = True  # execute() returns only after verified Cell teardown.
            async with self.sessions() as db:
                uncertain = await db.scalar(select(ToolExecutionReceipt.id).where(
                    ToolExecutionReceipt.request_id == task.id, ToolExecutionReceipt.status != "completed").limit(1))
            if uncertain:
                raise CoordinationError("effect_unknown", "A worker tool has an uncertain outcome; retain its claim")
            if event_failed:
                raise CoordinationError("event_delivery", "Execution stopped after event persistence failed")
            final_revision, _ = await asyncio.to_thread(inventory, target)
            payload = {"status": result.status, "report": result.report[:16000], "error": result.error,
                "workspace": str(target), "base_revision": revision, "revision": final_revision,
                "canonical_source_revision": canonical_revision,
                **({'cloud_environment': environment} if environment else {}),
                **({'checkpoint_base': previous} if previous else {}),
                "integration": "pending", "verification": "worker_report; inspect recorded tool results"}
            async with write_session(self.sessions) as db:
                await db.execute(update(ProjectTask).where(ProjectTask.id == task.id)
                    .values(status=result.status, result=payload))
                checkpoint_push = None
                if (result.status == 'succeeded' and not worker.read_only and not signal.is_set()
                        and environment and environment.get('target')):
                    checkpoint_push = await self.cloud.record_checkpoint(db, task, payload)
            if checkpoint_push:
                self.cloud.launch(self.cloud.push_checkpoint(*checkpoint_push))
            if claimed:
                await asyncio.to_thread(self.workshop.finish, source_claim, result.status, json.dumps(payload))
                claimed = False
            await self.coordinator.emit(run.id, "project_result", task.owner_agent,
                {**worker_metadata(task), "project_id": project.id,
                 "text": result.report[:16000], "artifact": payload})
            # Do not overwrite a newer owner's checkpoint or declare a project done.
            state = dict(checkpoint or {"objective": task.task, "acceptance": task.acceptance})
            prior_progress = str(state.get('progress', ''))[:3200]
            state.update(status="active", progress=(prior_progress + '\n' if prior_progress else '')
                + f"Task {task.id}: {result.status}; artifact {final_revision}",
                next_steps=list(dict.fromkeys([*state.get('next_steps', []),
                    "Review the retained artifact and integrate approved changes"]))[:30],
                blockers=[*state.get('blockers', []), *([] if result.status == "succeeded"
                    else [result.error or "Worker did not complete"])][:30],
                checks=[*state.get('checks', []),
                    f"Worker report for content revision {final_revision}; inspect execution receipts"][-30:])
            try:
                await asyncio.to_thread(self.workshop.checkpoint, Path(project.path),
                    revision=handoff["revision"], state=state)
            except ValueError:
                await self.coordinator.emit(run.id, "checkpoint_conflict", "system",
                    {**worker_metadata(task), "project_id": project.id,
                     "reason": "Newer checkpoint retained; result recorded separately"})
            if protocol:
                event = {"failed": "forge_execution_failed", "cancelled": "forge_execution_interrupted"}.get(result.status)
                protocol.finish(incident, event)
        except BaseException as exc:
            if protocol:
                event = "forge_execution_interrupted" if isinstance(exc, asyncio.CancelledError) else "forge_execution_error"
                if incident:
                    protocol.finish(incident, event, **exception_facts(exc))
                else:
                    protocol.capture({"event": event, "job_id": task_id, "ended_turn": True,
                                      **exception_facts(exc)})
            # An interrupted/unknown Cell keeps both claims. Never infer teardown
            # from a dead executor or from expiry of its lease.
            if claimed:
                await asyncio.shield(asyncio.to_thread(self.workshop.interrupt, source_claim, type(exc).__name__))
            async def hold():
                async with write_session(self.sessions) as db:
                    await db.execute(update(ProjectTask).where(ProjectTask.id == task_id).values(status="reconciling",
                        result={"reason": str(exc)[:1000] or "Execution interrupted", "teardown_confirmed": returned}))
            await asyncio.shield(hold())
            if task:
                await self.coordinator.emit(task.run_id,'project_held',task.owner_agent,
                    {**worker_metadata(task),'reason':str(exc)[:1000] or 'Execution interrupted','status':'reconciling'},task.parent_step)
        finally:
            self.signals.pop(task_id, None)

    async def stop_run(self, run_id):
        async with write_session(self.sessions) as db:
            tasks = list((await db.execute(select(ProjectTask).where(ProjectTask.run_id == run_id))).scalars())
            for task in tasks:
                task.cancelled = True
                if task.status == "queued":
                    task.status = "cancelled"
        for task in tasks:
            if task.id in self.signals:
                self.signals[task.id].set()
        await asyncio.gather(*(self.jobs[t.id] for t in tasks if t.id in self.jobs), return_exceptions=True)

    async def stop_task(self, owner, task_id, *, origin='owner', sender=None):
        accepted = False
        async with write_session(self.sessions) as db:
            task = await db.get(ProjectTask, task_id)
            run = await db.get(CoordinationRun, task.run_id) if task else None
            if not run or run.user_id != owner:
                raise CoordinationError("not_found", "Task unavailable", 404)
            if origin != 'owner' and (origin != 'agent' or sender != task.owner_agent):
                raise CoordinationError('worker_authority','Only the admitted supervisor may stop this project task')
            if task.status in {"queued", "staging", "running"} and not task.cancelled:
                room = await self.coordinator.owned_room(db, owner, run.room_id, lock=True)
                accepted = (await db.execute(update(ProjectTask).where(ProjectTask.id == task_id,
                    ProjectTask.status.in_(["queued", "staging", "running"]), ProjectTask.cancelled.is_(False))
                    .values(cancelled=True, status=case((ProjectTask.status == 'queued', 'cancelled'), else_=ProjectTask.status))
                    .returning(ProjectTask.id))).scalar_one_or_none() is not None
                if accepted:
                    await db.refresh(task)
                    await self.coordinator.event(db, room, "task_cancel_requested", sender if origin == 'agent' else "owner",
                        {**worker_metadata(task), "project_id": task.project_id, "scope": "task",'origin':origin,'sender_agent':sender,
                         'status':task.status}, run.id, task.parent_step)
        if accepted:
            await self.coordinator.notify()
            if task_id in self.signals:
                self.signals[task_id].set()
            if task_id in self.jobs:
                await asyncio.gather(self.jobs[task_id], return_exceptions=True)
        result = await self.inspect(owner, task.project_id, allow_standalone=True)
        result['task_control'] = {'task_id': task_id, 'accepted': accepted, 'scope': 'task'}
        return result

    async def steer(self, owner, task_id, body, *, origin='owner', sender=None):
        async with write_session(self.sessions) as db:
            task = await db.get(ProjectTask, task_id)
            run = await db.get(CoordinationRun, task.run_id) if task else None
            if not run or run.user_id != owner:
                raise CoordinationError("not_found", "Task unavailable", 404)
            if origin != 'owner' and (origin != 'agent' or sender != task.owner_agent):
                raise CoordinationError('worker_authority','Only the admitted supervisor may steer this project task')
            room = await self.coordinator.owned_room(db, owner, run.room_id, lock=True)
            await db.refresh(task)
            existing = await db.scalar(select(TaskInput).where(TaskInput.task_id == task_id,
                TaskInput.input_id == body.input_id))
            if existing:
                if existing.text != body.message:
                    raise CoordinationError("input_conflict", "Input ID belongs to another instruction")
                return {"id": existing.id, "status": existing.status}
            if task.cancelled or run.cancelled or task.status not in {"queued", "staging", "running"}:
                raise CoordinationError("task_closed", "Task is no longer accepting input")
            count = await db.scalar(select(func.count()).select_from(TaskInput).where(
                TaskInput.task_id == task_id, TaskInput.status.in_(["queued", "delivering"])))
            if count >= 16:
                raise CoordinationError("input_limit", "Worker already has sixteen waiting instructions")
            item = TaskInput(id=uuid4().hex, task_id=task_id, input_id=body.input_id, text=body.message)
            db.add(item)
            await db.flush()
            await self.coordinator.event(db, room, "task_input_queued", sender if origin == 'agent' else "owner",
                {**worker_metadata(task), "project_id": task.project_id,
                 "input_id": item.id, "message": body.message,'origin':origin,'sender_agent':sender}, run.id, task.parent_step)
            result = {"id": item.id, "status": item.status,
                "meaning": "Accepted; applied at the next safe execution checkpoint"}
        await self.coordinator.notify()
        return result
