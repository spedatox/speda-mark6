"""Owner-approved, revision-fenced deployment through trusted host recipes.

Workers never receive deployment credentials or a host shell. Preparing a release
copies an exact artifact into retained release storage; only the owner API can
execute the reviewed argv. Interrupted sends remain held for reconciliation.
"""
import asyncio
import json
import difflib
import os
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field
from app.database import write_session
from sqlalchemy import select, update
from app.models.coordination import ProjectOperation, ProjectTask, CoordinationRun, WorkerReview
from app.services.coordination_budget import CoordinationError, digest
from app.services.projects import inventory, snapshot


class ReleaseTarget(BaseModel):
    model_config = ConfigDict(extra='forbid')
    description: str = Field(min_length=1, max_length=1000)
    deploy: list[str] = Field(min_length=1, max_length=100)
    verify: list[str] = Field(min_length=1, max_length=100)
    environment: list[str] = Field(default_factory=list, max_length=40)
    timeout_seconds: int = Field(default=120, ge=1, le=900)
    rollback_instructions: str = Field(min_length=1, max_length=4000)


class ProjectRelease:
    def __init__(self, projects):
        self.projects, self.sessions = projects, projects.sessions

    async def answer(self, owner, ask_id, *, approved, remember=False):
        import re
        from app.schemas.coordination import ApproveRelease
        match = re.fullmatch(r'release:([a-f0-9]{32}):([a-f0-9]{64})', ask_id)
        if not match:
            raise CoordinationError('peer_retired', 'Peer permission expansion is retired; native release approvals require an exact plan', 410)
        if remember:
            raise CoordinationError('single_use_approval', 'Publication approval cannot be remembered', 422)
        return await self.decide(owner, match[1], ApproveRelease(plan_hash=match[2],
            decision='approve' if approved else 'reject'))

    async def targets(self):
        path = self.projects.settings.project_release_targets_file
        if not path:
            return []
        try:
            raw = await asyncio.to_thread(Path(path).read_text, encoding='utf-8')
            if len(raw.encode()) > 128000:
                raise ValueError('Target file too large')
            data = json.loads(raw)
            return [{'id': name, 'description': ReleaseTarget.model_validate(value).description}
                for name, value in list(data.items())[:30]]
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            raise CoordinationError('release_target', 'Trusted deployment target configuration is invalid', 422) from exc

    async def target(self, name):
        path = self.projects.settings.project_release_targets_file
        if not path:
            raise CoordinationError('publication_disabled', 'Configure trusted deployment targets before preparing publication')
        try:
            data = await asyncio.to_thread(Path(path).read_text, encoding='utf-8')
            if len(data.encode()) > 128000:
                raise ValueError('Target file exceeds 128 KB')
            target = ReleaseTarget.model_validate(json.loads(data)[name])
            for argv in (target.deploy, target.verify):
                if not Path(argv[0]).is_absolute() or not Path(argv[0]).is_file():
                    raise ValueError('Recipe executable must be an existing absolute file')
                if any('\x00' in arg or len(arg) > 8000 for arg in argv):
                    raise ValueError('Invalid recipe argument')
            if any(not key or '=' in key or '\x00' in key for key in target.environment):
                raise ValueError('Invalid environment name')
            return target
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise CoordinationError('release_target', 'Trusted deployment target is unavailable or invalid', 422) from exc

    async def prepare(self, context, body):
        await self.projects.coordinator.ensure_active(context)
        target = await self.target(body.target)
        async with self.sessions() as db:
            task = await db.get(ProjectTask, body.task_id)
            run = await db.get(CoordinationRun, task.run_id) if task else None
            review = await db.get(WorkerReview, body.task_id)
        if (not run or run.user_id != context.user_id or run.room_id != context.room_id
                or task.owner_agent != context.agent_id or context.task_role == 'worker'):
            raise CoordinationError('release_scope', 'Only the assigned supervisor may prepare publication')
        if (task.status != 'succeeded' or not review or review.verdict != 'accepted'
                or review.artifact_revision != body.artifact_revision
                or (task.result or {}).get('revision') != body.artifact_revision):
            raise CoordinationError('release_unreviewed', 'Publication requires supervisor acceptance of this exact completed artifact')
        project = await self.projects.owned(context.user_id, task.project_id)
        artifact = self.projects.state / 'artifacts' / task.id
        if Path(task.result['workspace']).resolve() != artifact.resolve():
            raise CoordinationError('artifact_changed', 'Artifact path differs from the registered execution')
        identity = uuid4().hex
        release = self.projects.state / 'releases' / identity
        actual = await asyncio.to_thread(snapshot, artifact, release)
        if actual != body.artifact_revision:
            raise CoordinationError('artifact_changed', 'Artifact content changed after review')
        source_revision, source_rows = await asyncio.to_thread(inventory, Path(project.path))
        baseline = self.projects.state / 'release-baselines' / identity
        if await asyncio.to_thread(snapshot, Path(project.path), baseline) != source_revision:
            raise CoordinationError('source_changed', 'Canonical source changed while preparing the rollback copy')
        _, release_rows = await asyncio.to_thread(inventory, release)
        old = {name: (sha, mode) for name, sha, mode in source_rows}
        new = {name: (sha, mode) for name, sha, mode in release_rows}
        changes = [{'path': name, 'change': 'delete' if name not in new else 'add' if name not in old else 'modify',
                    'before': old.get(name), 'after': new.get(name)}
            for name in sorted(old.keys() | new.keys()) if old.get(name) != new.get(name)]
        diff_text = await asyncio.to_thread(release_diff, baseline, release, changes)
        tokens = {'{workspace}': str(release), '{release_id}': identity, '{project_id}': project.id}
        plan = {'task_id': task.id, 'room_id': context.room_id, 'owner_agent': context.agent_id,
            'target': body.target, 'target_hash': digest(target.model_dump()), 'description': target.description,
            'artifact_revision': actual, 'source_revision': source_revision, 'workspace': str(release),
            'baseline_workspace': str(baseline), 'diff': diff_text,
            'acceptance': task.acceptance, 'supervisor_assessment': {'verdict': review.verdict,
                'rationale': review.rationale, 'check_ids': review.checks},
            'deploy': [tokens.get(arg, arg) for arg in target.deploy],
            'verify': [tokens.get(arg, arg) for arg in target.verify], 'environment_names': target.environment,
            'timeout_seconds': target.timeout_seconds, 'rollback_instructions': target.rollback_instructions,
            'review_version': review.version, 'expires_at': time.time() + 3600,
            'changes': changes, 'verification': 'Not deployed; owner approval required'}
        async with write_session(self.sessions) as db:
            db.add(ProjectOperation(id=identity, project_id=project.id, kind='deploy', status='awaiting_approval',
                details={'plan': plan, 'plan_hash': digest(plan)}))
            room = await self.projects.coordinator.owned_room(db, context.user_id, context.room_id, lock=True)
            await self.projects.coordinator.event(db, room, 'release_prepared', context.agent_id,
                {'operation_id': identity, 'project_id': project.id, 'target': body.target,
                 'revision': actual, 'plan_hash': digest(plan)}, context.run_id, context.step_id)
        await self.projects.coordinator.notify()
        return await self.inspect(context.user_id, identity)

    async def inspect(self, owner, identity):
        async with self.sessions() as db:
            row = await db.get(ProjectOperation, identity)
        if not row or row.kind != 'deploy':
            raise CoordinationError('not_found', 'Deployment approval unavailable', 404)
        await self.projects.owned(owner, row.project_id)
        return {'id': row.id, 'project_id': row.project_id, 'status': row.status, **row.details}

    async def decide(self, owner, identity, body):
        current = await self.inspect(owner, identity)
        if body.plan_hash != current['plan_hash']:
            raise CoordinationError('approval_changed', 'Approval must name the exact reviewed plan')
        if current['status'] != 'awaiting_approval':
            return current  # Approval retries never execute another command.
        plan = current['plan']
        if digest(plan) != current['plan_hash']:
            raise CoordinationError('approval_changed', 'Stored release plan does not match its approval hash')
        if body.decision == 'approve':
            if plan['expires_at'] <= time.time():
                raise CoordinationError('approval_expired', 'Release approval expired; prepare a new plan')
            target = await self.target(plan['target'])
            if digest(target.model_dump()) != plan['target_hash']:
                raise CoordinationError('release_target_changed', 'Deployment target changed; prepare a new approval')
            if (await asyncio.to_thread(inventory, Path(plan['workspace'])))[0] != plan['artifact_revision']:
                raise CoordinationError('artifact_changed', 'Retained release changed since preparation')
            if (await asyncio.to_thread(inventory, Path(plan['baseline_workspace'])))[0] != plan['source_revision']:
                raise CoordinationError('baseline_changed', 'Retained rollback baseline changed; prepare a new plan')
            project = await self.projects.owned(owner, current['project_id'])
            if (await asyncio.to_thread(inventory, Path(project.path)))[0] != plan['source_revision']:
                raise CoordinationError('source_changed', 'Canonical source changed; prepare a new release plan')
            async with self.sessions() as db:
                review = await db.get(WorkerReview, plan['task_id'])
            if not review or review.version != plan['review_version'] or review.verdict != 'accepted':
                raise CoordinationError('review_changed', 'Supervisor assessment changed; prepare a new approval')
        async with write_session(self.sessions) as db:
            # Lock project before checking another in-flight deployment.
            from app.models.coordination import Project
            await db.execute(update(Project).where(Project.id == current['project_id']).values(location_version=Project.location_version))
            if body.decision == 'approve':
                busy = await db.scalar(select(ProjectOperation.id).where(
                    ProjectOperation.project_id == current['project_id'], ProjectOperation.kind == 'deploy',
                    ProjectOperation.status.in_(['executing', 'reconciling'])).limit(1))
                if busy:
                    raise CoordinationError('deployment_held', 'Another deployment is active or needs reconciliation')
            changed = await db.execute(update(ProjectOperation).where(ProjectOperation.id == identity,
                ProjectOperation.status == 'awaiting_approval').values(
                    status='executing' if body.decision == 'approve' else 'rejected',
                    details={**{key: value for key, value in current.items() if key not in {'id', 'project_id', 'status'}},
                        'owner_decision': {'user_id': owner, 'decision': body.decision,
                            'at': datetime.utcnow().isoformat() + 'Z', 'plan_hash': body.plan_hash}}))
        if not changed.rowcount or body.decision == 'reject':
            return await self.inspect(owner, identity)
        # Durable approval/effect receipt committed before launching any process.
        outcomes = []
        try:
            for stage in ('deploy', 'verify'):
                outcome = await self.command(plan[stage], plan)
                outcomes.append({'stage': stage, **outcome})
                if outcome['returncode'] != 0:
                    raise CoordinationError('deployment_failed', 'Deployment or health verification failed; reconcile external state')
            if (await asyncio.to_thread(inventory, Path(plan['workspace'])))[0] != plan['artifact_revision']:
                raise CoordinationError('release_mutated', 'Deployment driver changed its approved input; reconcile the deployed revision')
            status = 'complete'
        except BaseException as exc:
            await asyncio.shield(self.settle(identity, 'reconciling', outcomes, type(exc).__name__))
            raise
        await self.settle(identity, status, outcomes)
        return await self.inspect(owner, identity)

    async def pending(self, owner):
        from app.models.coordination import Project, Room
        async with self.sessions() as db:
            rows = list(await db.scalars(select(ProjectOperation).join(Project).where(
                Project.user_id == owner, ProjectOperation.kind == 'deploy',
                ProjectOperation.status == 'awaiting_approval').order_by(ProjectOperation.id).limit(50)))
            rooms = {r.id: r for r in await db.scalars(select(Room).where(Room.user_id == owner))}
        asks = []
        for row in rows:
            plan, plan_hash = row.details['plan'], row.details['plan_hash']
            seconds = max(0, plan['expires_at'] - time.time())
            if not seconds:
                continue
            room = rooms.get(plan['room_id'])
            asks.append({'ask_id': f'release:{row.id}:{plan_hash}', 'kind': 'forge_release',
                'agent_id': plan['owner_agent'], 'tool': 'publish ' + plan['target'],
                'action_key': json.dumps(plan, ensure_ascii=False, indent=2),
                'reason': 'Review this exact release, deployment command, health check and rollback instructions. Approval applies once to this plan only.',
                'job_id': row.id, 'chat_id': str(room.session_id) if room else None, 'seconds_left': seconds})
        return asks

    async def recover(self):
        """Startup only: never infer that a prior host deployment stopped."""
        async with write_session(self.sessions) as db:
            result = await db.execute(update(ProjectOperation).where(ProjectOperation.kind == 'deploy',
                ProjectOperation.status == 'executing').values(status='reconciling'))
        return {'deployments_held': result.rowcount}

    async def reconcile(self, owner, identity, body):
        current = await self.inspect(owner, identity)
        if current['status'] != 'reconciling' or current['plan_hash'] != body.plan_hash:
            raise CoordinationError('reconciliation_conflict', 'Only this exact held deployment may be reconciled')
        plan = current['plan']
        outcomes = list(current.get('outcomes', []))
        if body.outcome == 'published':
            target = await self.target(plan['target'])
            if digest(target.model_dump()) != plan['target_hash']:
                raise CoordinationError('release_target_changed', 'Verify against the original trusted recipe before reconciliation')
            result = await self.command(plan['verify'], plan)
            outcomes.append({'stage': 'reconcile_verify', **result})
            if result['returncode'] != 0:
                raise CoordinationError('verification_failed', 'Deployment verification did not pass; hold remains')
        async with write_session(self.sessions) as db:
            row = await db.get(ProjectOperation, identity)
            if row.status != 'reconciling':
                raise CoordinationError('reconciliation_conflict', 'Deployment status changed')
            row.status = 'complete' if body.outcome == 'published' else 'failed'
            row.details = {**row.details, 'outcomes': outcomes, 'operator_reconciliation': {
                'user_id': owner, 'at': datetime.utcnow().isoformat() + 'Z',
                **body.model_dump(), 'closure_verification': 'Owner attestation; not inferred from lease expiry'}}
        await self.projects.coordinator.notify()
        return await self.inspect(owner, identity)

    async def settle(self, identity, status, outcomes, error=None):
        async with write_session(self.sessions) as db:
            row = await db.get(ProjectOperation, identity)
            row.status = status
            row.details = {**row.details, 'outcomes': outcomes, 'error': error}
            plan = row.details['plan']
            run = await db.get(CoordinationRun, (await db.get(ProjectTask, plan['task_id'])).run_id)
            room = await self.projects.coordinator.owned_room(db, run.user_id, plan['room_id'], lock=True)
            await self.projects.coordinator.event(db, room, 'release_finished', 'system',
                {'operation_id': identity, 'status': status, 'error': error}, run.id)
        # State is durable even if a notification transport is disconnected.
        await self.projects.coordinator.notify()

    async def command(self, argv, plan):
        # Only named host environment values enter the trusted deployment driver.
        environment = {key: os.environ[key] for key in plan['environment_names'] if key in os.environ}
        for key in ('SystemRoot', 'WINDIR'):
            if key in os.environ:
                environment[key] = os.environ[key]
        process = await asyncio.create_subprocess_exec(*argv, cwd=plan['workspace'], env=environment,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        async def collect():
            data = bytearray()
            total = 0
            while chunk := await process.stdout.read(8192):
                total += len(chunk)
                if len(data) < 16000:
                    data.extend(chunk[:16000-len(data)])
            return data.decode('utf-8', errors='replace'), total > 16000
        reader = asyncio.create_task(collect())
        try:
            async with asyncio.timeout(plan['timeout_seconds']):
                await process.wait()
                output, truncated = await reader
        except BaseException:
            if process.returncode is None:
                process.kill()
            reader.cancel()
            await asyncio.gather(reader, process.wait(), return_exceptions=True)
            raise
        for value in environment.values():
            if len(value) >= 4:
                output = output.replace(value, '[redacted]')
        return {'returncode': process.returncode, 'output': output, 'truncated': truncated}


def release_diff(baseline, release, changes):
    """Bounded plain-text review evidence; hashes cover omitted/binary content."""
    parts, remaining, omitted = [], 64000, 0
    for change in changes:
        if remaining <= 0:
            omitted += 1
            continue
        name = change['path']
        paths = [baseline / name, release / name]
        if any(p.exists() and p.stat().st_size > 128000 for p in paths):
            parts.append(f'{name}: large file; inspect retained baseline and release; hashes in manifest\n')
            remaining -= len(parts[-1].encode())
            continue
        try:
            before, after = [p.read_text(encoding='utf-8') if p.exists() else '' for p in paths]
            if '\x00' in before + after:
                raise UnicodeError()
            diff = ''.join(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                fromfile='baseline/' + name, tofile='release/' + name))
            encoded = diff.encode()
            if len(encoded) > remaining:
                diff = encoded[:remaining].decode(errors='ignore') + '\n[Diff truncated; inspect retained files]\n'
            parts.append(diff)
            remaining -= len(encoded)
        except UnicodeError:
            parts.append(f'{name}: binary content; hashes in manifest\n')
            remaining -= len(parts[-1].encode())
    return ''.join(parts) + (f'\n{omitted} additional changed files omitted from diff; full manifest retained.' if omitted else '')
