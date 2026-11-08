"""Folder-bound project UI and context over the existing Coordinator/Workshop.

No execution queue, memory writer, scheduler or alternative checkpoint lives here.
All reads are owner/agent scoped. Folder writes use the existing source lifecycle.
"""
import asyncio
from datetime import datetime
import os
import time
from pathlib import Path
from app.database import write_session
from sqlalchemy import select, func, update
from sqlalchemy.exc import IntegrityError
from app.models.client_compat import ClientProject, ClientProjectFile, ClientSession
from app.models.coordination import Project, ProjectTask, ProjectOperation, Room, CoordinationRun
from app.models.session import Session
from app.schemas.coordination import CreateProject, IntegrateProject
from app.services.coordination_budget import CoordinationError
from app.services.projects import inventory, IGNORED
from app.services.room_attachments import prepare_client_attachment


class ProjectWorkspace:
    def __init__(self, coordinator):
        self.coordinator, self.sessions = coordinator, coordinator.sessions
        self.projects = coordinator.projects

    def agent(self, agent):
        if agent == 'warroom':
            return 'warroom'
        return self.coordinator.profiles.require(agent).agent_id

    def configured(self):
        if self.projects is None:
            raise CoordinationError('project_unconfigured', 'Configure project workspace and state roots in Settings', 503)
        self.projects.configured()

    async def owned(self, db, owner, agent, identity, *, lock=False):
        stmt = select(ClientProject).where(ClientProject.id == identity,
            ClientProject.user_id == owner, ClientProject.agent_id == self.agent(agent))
        if lock:
            stmt = stmt.with_for_update()
        row = await db.scalar(stmt)
        if row is None:
            raise CoordinationError('not_found', 'Project unavailable for this agent', 404)
        return row

    async def create(self, owner, body):
        self.configured()
        agent = self.agent(body.agent_id)
        native = await self.projects.create(owner, CreateProject(name=body.name, path=body.path))
        try:
            async with write_session(self.sessions) as db:
                row = await db.scalar(select(ClientProject).where(ClientProject.user_id == owner,
                    ClientProject.agent_id == agent, ClientProject.native_id == native['id']))
                if row is None:
                    row = ClientProject(user_id=owner, agent_id=agent, native_id=native['id'],
                        **body.model_dump(exclude={'agent_id', 'path'}))
                    db.add(row)
                    await db.flush()
                else:
                    row.removed, row.archived = False, False
                identity = row.id
        except IntegrityError:
            async with self.sessions() as db:
                identity = await db.scalar(select(ClientProject.id).where(ClientProject.user_id == owner,
                    ClientProject.agent_id == agent, ClientProject.native_id == native['id']))
                if identity is None:
                    raise
        return await self.get(owner, agent, identity)

    async def view(self, db, row):
        native = await db.get(Project, row.native_id)
        cloud = await db.scalar(select(ProjectOperation.id).where(ProjectOperation.project_id == row.native_id,
            ProjectOperation.kind == 'cloud_environment').limit(1))
        sessions = list(await db.scalars(select(Session).join(Room, Room.session_id == Session.id)
            .outerjoin(ClientSession, ClientSession.session_id == Session.id).where(
                Room.project_id == row.native_id, Room.user_id == row.user_id,
                Session.agent_id == row.agent_id,
                (ClientSession.hidden.is_(None)) | (ClientSession.hidden.is_(False)))))
        count = await db.scalar(select(func.count()).select_from(ClientProjectFile).where(
            ClientProjectFile.project_id == row.id, ClientProjectFile.removed.is_(False)))
        activity = max([row.updated_at, *[s.started_at.replace(tzinfo=None) for s in sessions]])
        return {key: getattr(row, key) for key in ('id', 'agent_id', 'name', 'description',
            'instructions', 'icon', 'color', 'pinned', 'archived', 'version')} | {
            'workshop_project_id': row.native_id, 'cwd': native.path, 'host_id': native.host_id, 'cloud': bool(cloud),
            'created_at': row.created_at.isoformat() + 'Z', 'updated_at': row.updated_at.isoformat() + 'Z',
            'last_activity_at': activity.isoformat() + 'Z', 'chat_count': len(sessions), 'file_count': count}

    async def get(self, owner, agent, identity):
        async with self.sessions() as db:
            return await self.view(db, await self.owned(db, owner, agent, identity))

    async def listing(self, owner, agent, archived=False):
        async with self.sessions() as db:
            stmt = select(ClientProject).where(ClientProject.user_id == owner,
                ClientProject.agent_id == self.agent(agent), ClientProject.removed.is_(False))
            if not archived:
                stmt = stmt.where(ClientProject.archived.is_(False))
            rows = list(await db.scalars(stmt.order_by(ClientProject.pinned.desc(), ClientProject.updated_at.desc())))
            return [await self.view(db, row) for row in rows]

    async def patch(self, owner, agent, identity, body):
        async with write_session(self.sessions) as db:
            row = await self.owned(db, owner, agent, identity, lock=True)
            if body.expected_version is not None and row.version != body.expected_version:
                raise CoordinationError('project_changed', 'Project settings changed; reload before saving', 409)
            values = body.model_dump(exclude_none=True, exclude={'expected_version'})
            result = await db.execute(update(ClientProject).where(ClientProject.id == row.id,
                ClientProject.version == row.version).values(**values, version=row.version + 1,
                    updated_at=datetime.utcnow()))
            if not result.rowcount:
                raise CoordinationError('project_changed', 'Project settings changed; reload before saving', 409)
        return await self.get(owner, agent, identity)

    async def remove(self, owner, agent, identity):
        async with write_session(self.sessions) as db:
            row = await self.owned(db, owner, agent, identity)
            row.removed, row.archived = True, True
        return {'removed': True, 'files_preserved': True, 'chats_preserved': True}

    async def sessions_for(self, owner, agent, identity):
        async with self.sessions() as db:
            row = await self.owned(db, owner, agent, identity)
        from app.services.client_compat import ClientCompatibility
        return await ClientCompatibility(self.coordinator).list_sessions(owner, agent, 500,
            project_native_id=row.native_id)

    async def resolve(self, owner, agent, identity, *, new_chat=False):
        self.configured()
        async with self.sessions() as db:
            row = await self.owned(db, owner, agent, identity)
            if new_chat and (row.archived or row.removed):
                raise CoordinationError('project_archived', 'Restore this project before starting a new chat', 409)
        await self.projects.owned(owner, row.native_id)
        return row.native_id

    async def context(self, owner, agent, native_id):
        """Private project instructions and retained documents, never sibling chats."""
        native = await self.projects.owned(owner, native_id)
        async with self.sessions() as db:
            row = await db.scalar(select(ClientProject).where(ClientProject.user_id == owner,
                ClientProject.agent_id == self.agent(agent), ClientProject.native_id == native_id))
            if row is None:
                return {'cwd': native.path, 'host_id': native.host_id, 'native_id': native_id}
            files = list(await db.scalars(select(ClientProjectFile).where(
                ClientProjectFile.project_id == row.id, ClientProjectFile.removed.is_(False))
                .order_by(ClientProjectFile.id)))
            remaining, documents = 16000, []
            for item in files:
                excerpt = item.extracted[:remaining]
                if not excerpt:
                    break
                documents.append({'name': item.name, 'text': excerpt, 'truncated': len(excerpt) < len(item.extracted)})
                remaining -= len(excerpt)
            return {'id': row.id, 'name': row.name, 'instructions': row.instructions,
                'cwd': native.path, 'host_id': native.host_id, 'native_id': native_id,
                'documents': documents, 'documents_omitted': len(files) - len(documents)}

    async def read(self, context, path, start_line=1, max_lines=200, task_id=None):
        """Bounded code reads through the same snapshot inventory as execution."""
        native = await self.projects.owned(context.user_id, context.project_id)
        root = Path(native.path)
        project = getattr(context, 'extra', {}).get('project_context') or {}
        checkpoint = project.get('cloud_checkpoint') if not task_id else None
        if checkpoint:
            task_id = checkpoint['task_id']
        if not task_id:
            pinned = project.get('cloud_environment')
            if not pinned:
                async with self.sessions() as db:
                    pinned = await self.projects.cloud.binding(db, native.id, allow_draft=True)
            if pinned:
                root = await self.projects.cloud.source(pinned)
        if task_id:
            async with self.sessions() as db:
                task = await db.get(ProjectTask, task_id)
                run = await db.get(CoordinationRun, task.run_id) if task else None
                if (not run or run.user_id != context.user_id or task.project_id != context.project_id
                        or task.owner_agent != context.agent_id or task.status != 'succeeded'):
                    raise CoordinationError('artifact_unavailable', 'Artifact is outside this supervisor project', 404)
            root = self.projects.state / 'artifacts' / task_id
        def read():
            revision, rows = inventory(root)
            if checkpoint and revision != checkpoint['artifact_revision']:
                raise CoordinationError('stale_artifact', 'The published checkpoint artifact changed after verification')
            if not path:
                return {'revision':revision, 'files':[r[0] for r in rows[:1000]], 'omitted':max(0,len(rows)-1000)}
            if path not in {r[0] for r in rows}:
                raise CoordinationError('project_file_scope', 'Choose a file from this project inventory', 422)
            target = root / path
            if target.stat().st_size > 512000:
                raise CoordinationError('project_file_size', 'File exceeds the 512 KiB text preview limit', 422)
            try:
                text = target.read_text(encoding='utf-8')
                if '\x00' in text:
                    raise UnicodeError()
            except UnicodeError as exc:
                raise CoordinationError('project_file_binary', 'This file needs a binary viewer', 422) from exc
            lines = text.splitlines()
            selected = '\n'.join(f'{n+1}: {line}' for n,line in enumerate(lines)
                if start_line-1 <= n < start_line-1+max_lines)
            return {'path':path, 'revision':revision, 'start_line':start_line, 'total_lines':len(lines),
                'content':selected[:32000], 'truncated':len(selected)>32000 or len(lines)>start_line-1+max_lines}
        return await asyncio.to_thread(read)

    def root(self):
        self.configured()
        root = self.projects.root
        if not root.is_dir():
            raise CoordinationError('workspace_missing', 'Configured workspace root does not exist', 503)
        return root

    def directory(self, path):
        root = self.root()
        target = Path(path).resolve(strict=True) if path else root
        if not target.is_relative_to(root) or not target.is_dir():
            raise CoordinationError('project_scope', 'Choose a folder under the configured workspace root', 422)
        if any(p in IGNORED or p.startswith('.') for p in target.relative_to(root).parts):
            raise CoordinationError('project_scope', 'Execution and repository metadata cannot be projects', 422)
        return target

    async def folders(self, path=''):
        def read():
            try:
                target = self.directory(path)
                dirs = sorted((p for p in target.iterdir() if p.is_dir() and not p.is_symlink()
                    and not (hasattr(p, 'is_junction') and p.is_junction())
                    and p.name not in IGNORED and not p.name.startswith('.')), key=lambda p: p.name.casefold())
                return {'path': str(target), 'root': str(self.root()),
                    'parent': str(target.parent) if target != self.root() else None,
                    'directories': [{'name': p.name, 'path': str(p)} for p in dirs[:500]],
                    'truncated': len(dirs) > 500, 'host_id': self.projects.settings.project_host_id}
            except OSError as exc:
                raise CoordinationError('folder_unavailable', 'Folder is unavailable on the execution host', 404) from exc
        return await asyncio.to_thread(read)

    async def mkdir(self, body):
        def create():
            name = body.name.strip()
            if (not name or name.startswith('.') or name in IGNORED or name.rstrip('. ') != name
                    or any(c in name for c in '/\\:<>|?*') or any(ord(c) < 32 for c in name)):
                raise CoordinationError('folder_name', 'Use a visible folder name without path separators', 422)
            try:
                target = self.directory(body.parent) / name
                target.mkdir()
                return {'path': str(target)}
            except FileExistsError as exc:
                raise CoordinationError('folder_exists', 'This folder already exists', 409) from exc
            except OSError as exc:
                raise CoordinationError('folder_unavailable', 'Could not create folder', 422) from exc
        return await asyncio.to_thread(create)

    async def files(self, owner, agent, identity):
        async with self.sessions() as db:
            await self.owned(db, owner, agent, identity)
            rows = await db.scalars(select(ClientProjectFile).where(ClientProjectFile.project_id == identity,
                ClientProjectFile.removed.is_(False)).order_by(ClientProjectFile.id.desc()))
            return [self.file_view(row) for row in rows]

    @staticmethod
    def file_view(row):
        return {'id': row.id, 'name': row.name, 'media_type': row.mime, 'size': len(row.original),
            'chars': len(row.extracted), 'created_at': row.created_at.isoformat() + 'Z'}

    async def upload(self, owner, agent, identity, body):
        # Check access before parsing any supplied document.
        async with self.sessions() as db:
            await self.owned(db, owner, agent, identity)
        prepared = await asyncio.to_thread(prepare_client_attachment, body)
        async with write_session(self.sessions) as db:
            row = await self.owned(db, owner, agent, identity, lock=True)
            # A version CAS serializes concurrent file admission on SQLite too.
            changed = await db.execute(update(ClientProject).where(ClientProject.id == row.id,
                ClientProject.version == row.version).values(version=row.version + 1, updated_at=datetime.utcnow()))
            if not changed.rowcount:
                raise CoordinationError('project_changed', 'Retry upload after concurrent project change', 409)
            count = await db.scalar(select(func.count()).select_from(ClientProjectFile).where(
                ClientProjectFile.project_id == identity, ClientProjectFile.removed.is_(False)))
            if count >= 20:
                raise CoordinationError('project_files_full', 'Project reference limit is 20 files', 422)
            item = ClientProjectFile(project_id=identity, **{k: prepared[k] for k in ('name','mime','original','extracted')})
            db.add(item)
            await db.flush()
            return self.file_view(item)

    async def remove_file(self, owner, agent, identity, file_id):
        async with write_session(self.sessions) as db:
            await self.owned(db, owner, agent, identity)
            row = await db.get(ClientProjectFile, file_id)
            if not row or row.project_id != identity:
                raise CoordinationError('not_found', 'Project reference unavailable', 404)
            row.removed = True
        return {'removed': True, 'original_preserved': True}

    async def state(self, owner, agent, identity):
        native_id = await self.resolve(owner, agent, identity)
        native = await self.projects.owned(owner, native_id)
        async with self.sessions() as db:
            environment = await self.projects.cloud.binding(db, native_id, allow_draft=True)
        source = await self.projects.cloud.source(environment) if environment else Path(native.path)
        revision, manifest = await asyncio.to_thread(inventory, source)
        workshop = await asyncio.to_thread(self.projects.workshop.inspect, native.legacy_id)
        async with self.sessions() as db:
            tasks = list(await db.scalars(select(ProjectTask).where(ProjectTask.project_id == native_id,
                ProjectTask.owner_agent == self.agent(agent)).order_by(ProjectTask.id).limit(100)))
            runs = {r.id:r for r in await db.scalars(select(CoordinationRun).where(
                CoordinationRun.id.in_([t.run_id for t in tasks])))}
            operations = list(await db.scalars(select(ProjectOperation).where(ProjectOperation.project_id == native_id,
                ProjectOperation.kind.in_(['integrate','rollback'])).order_by(ProjectOperation.id.desc()).limit(50)))
        return {'project_id': identity, 'cwd': native.path, 'host_id': native.host_id,
            'execution_enabled': self.projects.enabled,
            'environment': environment,
            'source_revision': revision, 'files': [r[0] for r in manifest[:1000]],
            'files_omitted': max(0, len(manifest)-1000), 'checkpoint': workshop.get('checkpoint'),
            'workshop_revision': workshop['revision'], 'claims': workshop.get('active_runs', []),
            'tasks': [{'id': t.id, 'task': t.task, 'status': 'unknown' if t.status in {'running','staging','queued'}
                and (runs[t.run_id].executor != self.coordinator.identity or runs[t.run_id].lease_until <= time.time())
                else t.status, 'stored_status': t.status, 'result': t.result,
                'base_revision': t.base_revision} for t in tasks],
            'operations': [{'id': o.id, 'kind': o.kind, 'status': o.status, 'details': o.details} for o in operations],
            'git': {'available': False, 'status': '', 'truncated': False} if environment
            else await self.git_status(native.path)}

    @staticmethod
    async def git_status(path):
        try:
            process = await asyncio.create_subprocess_exec('git', '-C', str(path), 'status', '--porcelain=v1', '-z',
                '--untracked-files=normal', stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env={**os.environ, 'GIT_OPTIONAL_LOCKS':'0', 'GIT_TERMINAL_PROMPT':'0'})
            try:
                # Keep draining after the preview cap so a large status cannot deadlock.
                async def drain(reader):
                    data = bytearray()
                    while chunk := await reader.read(8192):
                        if len(data) < 64000:
                            data.extend(chunk[:64000-len(data)])
                    return bytes(data)
                out, err = await asyncio.wait_for(asyncio.gather(drain(process.stdout), drain(process.stderr)), 10)
                await process.wait()
            except BaseException:
                if process.returncode is None:
                    process.kill()
                await process.wait()
                raise
            return {'available': process.returncode == 0, 'status': out.decode(errors='replace').replace('\x00','\n'),
                'truncated': len(out) >= 64000}
        except (OSError, asyncio.TimeoutError):
            return {'available': False, 'status': '', 'truncated': False}

    async def changes(self, owner, agent, identity, task_id):
        native_id = await self.resolve(owner, agent, identity)
        native = await self.projects.owned(owner, native_id)
        async with self.sessions() as db:
            task = await db.get(ProjectTask, task_id)
            if not task or task.project_id != native_id or task.owner_agent != self.agent(agent) or task.status != 'succeeded':
                raise CoordinationError('artifact_unavailable', 'Completed project artifact unavailable', 404)
        artifact = self.projects.state / 'artifacts' / task.id
        if Path((task.result or {}).get('workspace', '')).resolve() != artifact.resolve():
            raise CoordinationError('artifact_changed', 'Artifact location differs from its execution', 409)
        environment = (task.result or {}).get('cloud_environment')
        source = await self.projects.cloud.source(environment) if environment else Path(native.path)
        before, old_rows = await asyncio.to_thread(inventory, source)
        after, new_rows = await asyncio.to_thread(inventory, artifact)
        if after != task.result.get('revision'):
            raise CoordinationError('artifact_changed', 'Artifact content changed', 409)
        old, new = ({n:(sha,mode) for n,sha,mode in rows} for rows in (old_rows,new_rows))
        changes = [{'path':n, 'change':'delete' if n not in new else 'add' if n not in old else 'modify',
            'before':old.get(n), 'after':new.get(n)} for n in sorted(old.keys() | new.keys()) if old.get(n) != new.get(n)]
        from app.services.project_release import release_diff
        diff = await asyncio.to_thread(release_diff, source, artifact, changes)
        return {'task_id':task.id, 'expected_source_revision':before, 'expected_artifact_revision':after,
            'base_revision':task.base_revision,
            'source_changed_since_execution':before != task.result.get('canonical_source_revision', task.base_revision),
            'changes':changes, 'diff':diff, 'environment':environment}

    async def integrate(self, owner, agent, identity, body):
        review = await self.changes(owner, agent, identity, body.task_id)
        if review['environment']:
            raise CoordinationError('cloud_pr_required', 'Open a pull request for changes from a cloud environment', 409)
        if review['source_changed_since_execution']:
            raise CoordinationError('source_changed', 'Folder changed since this task started; reconcile changes in a new task before applying', 409)
        from app.services.project_lifecycle import ProjectLifecycle
        return await ProjectLifecycle(self.projects).integrate(owner,
            await self.resolve(owner, agent, identity), body)

    async def rollback(self, owner, agent, identity, operation_id, expected_revision):
        native_id = await self.resolve(owner, agent, identity)
        async with self.sessions() as db:
            row = await db.get(ProjectOperation, operation_id)
            if not row or row.project_id != native_id:
                raise CoordinationError('not_found', 'Project operation unavailable', 404)
        from app.services.project_lifecycle import ProjectLifecycle
        return await ProjectLifecycle(self.projects).rollback(owner, operation_id, expected_revision)
