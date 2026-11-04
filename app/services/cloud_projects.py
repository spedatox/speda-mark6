"""Published GitHub environments and pull requests over native project operations."""
import asyncio
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select, update

from app.database import write_session
from app.models.coordination import ProjectOperation, ProjectTask
from app.schemas.project_workspace import WorkspaceCreate
from app.services.coordination_budget import CoordinationError, digest
from forge.cell.base import CellCleanupError, CellPolicy
from forge.cell.factory import build_cell
from forge.tools.gitpush import _CRED_HELPER, _HARDEN, _TOKEN_ENV, _github_api, _token


class CloudEnvironments:
    def __init__(self, projects, *, build=build_cell, github_api=_github_api):
        self.projects = projects
        self.sessions = projects.sessions
        self.build = build
        self.github_api = github_api
        self.jobs = set()

    def workspace(self):
        from app.services.project_workspace import ProjectWorkspace
        return ProjectWorkspace(self.projects.coordinator)

    def root(self, identity):
        return self.projects.state / 'cloud' / identity

    def credential(self, agent):
        return _token(agent) or self.projects.settings.github_token

    async def recover(self):
        # A lost executor leaves evidence. Restart never repeats a remote write.
        async with write_session(self.sessions) as db:
            await db.execute(update(ProjectOperation).where(
                ProjectOperation.kind.in_(['cloud_environment', 'cloud_pr']),
                ProjectOperation.status.in_(['preparing', 'executing'])
            ).values(status='reconciling'))

    async def shutdown(self):
        for job in list(self.jobs):
            job.cancel()
        await asyncio.gather(*self.jobs, return_exceptions=True)

    def launch(self, coroutine):
        job = asyncio.create_task(coroutine)
        self.jobs.add(job)
        job.add_done_callback(self.jobs.discard)

    async def settle(self, identity, status, details):
        async with write_session(self.sessions) as db:
            await db.execute(update(ProjectOperation).where(ProjectOperation.id == identity)
                .values(status=status, details=details))

    @staticmethod
    def view(row):
        return {'id': row.id, 'status': row.status, **row.details}

    async def status(self, owner, agent, project_id):
        native_id = await self.workspace().resolve(owner, agent, project_id)
        async with self.sessions() as db:
            rows = list(await db.scalars(select(ProjectOperation).where(
                ProjectOperation.project_id == native_id,
                ProjectOperation.kind.in_(['cloud_environment', 'cloud_pr']))))
        rows = [r for r in rows if r.details.get('agent_id') == self.workspace().agent(agent)]
        environments = sorted((r for r in rows if r.kind == 'cloud_environment'),
            key=lambda r: r.details['created_at'], reverse=True)
        return {'environments': [self.view(r) for r in environments[:20]],
            'pull_requests': [self.view(r) for r in rows if r.kind == 'cloud_pr'][:100]}

    async def binding(self, db, native_id, *, allow_draft=False):
        if not native_id:
            return None
        rows = list(await db.scalars(select(ProjectOperation).where(
            ProjectOperation.project_id == native_id, ProjectOperation.kind == 'cloud_environment')))
        if not rows:
            return None
        published = [r for r in rows if r.status == 'published']
        if not published:
            if allow_draft:
                return None
            raise CoordinationError('cloud_unpublished', 'Publish a prepared cloud environment before starting a task', 409)
        row = max(published, key=lambda r: r.details['published_at'])
        return {'id': row.id, **{key: row.details[key] for key in (
            'repository', 'branch', 'commit', 'image', 'source_revision')}}

    async def prepare(self, owner, body, project_id=None):
        workspace = self.workspace()
        workspace.configured()
        agent = workspace.agent(body.agent_id)
        request_hash = digest(body.model_dump())
        identity = uuid5(NAMESPACE_URL, f'speda:cloud:{owner}:{agent}:{body.request_id}').hex
        async with self.sessions() as db:
            prior = await db.get(ProjectOperation, identity)
        if prior:
            if (prior.kind != 'cloud_environment' or prior.details.get('request_hash') != request_hash
                    or project_id is not None and prior.details['project_id'] != project_id):
                raise CoordinationError('cloud_request_conflict', 'This request ID belongs to another environment', 409)
            return self.view(prior)
        if project_id is None:
            folder = workspace.root() / ('cloud-' + identity)
            folder.mkdir(exist_ok=True)
            project = await workspace.create(owner, WorkspaceCreate(agent_id=agent,
                name=body.name, path=str(folder), description=f'{body.repository} · {body.branch}'))
            project_id, native_id = project['id'], project['workshop_project_id']
        else:
            native_id = await workspace.resolve(owner, agent, project_id, new_chat=True)
            async with self.sessions() as db:
                first = await db.scalar(select(ProjectOperation.id).where(
                    ProjectOperation.project_id == native_id, ProjectOperation.kind == 'cloud_environment').limit(1))
            if not first:
                raise CoordinationError('cloud_project_required', 'Create a cloud project for repository environments', 422)
        details = {'agent_id': agent, 'project_id': project_id, 'repository': body.repository,
            'branch': body.branch, 'check': body.check, 'request_hash': request_hash,
            'created_at': datetime.now(timezone.utc).isoformat()}
        async with write_session(self.sessions) as db:
            prior = await db.get(ProjectOperation, identity)
            if prior:
                if prior.details.get('request_hash') != request_hash or prior.project_id != native_id:
                    raise CoordinationError('cloud_request_conflict', 'This request ID belongs to another environment', 409)
                return self.view(prior)
            db.add(ProjectOperation(id=identity, project_id=native_id,
                kind='cloud_environment', status='preparing', details=details))
        self.launch(self.prepare_environment(identity, details))
        return {'id': identity, 'status': 'preparing', **details}

    async def prepare_environment(self, identity, details):
        from app.services.projects import cell_image_for, inventory, snapshot

        try:
            image_ref = cell_image_for(self.projects.settings, 'coder')
            if not image_ref:
                raise CoordinationError('cloud_image_required', 'Configure a trusted coder Cell image before preparing an environment')
            image = (await self.command('docker', 'image', 'inspect', '--format', '{{.Id}}', '--', image_ref)).strip()
            if not re.fullmatch(r'sha256:[a-f0-9]{64}', image):
                raise CoordinationError('cloud_image', 'Could not resolve an immutable Docker image ID')
            root = self.root(identity)
            root.mkdir(parents=True, exist_ok=False)
            checkout = root / 'repository'
            url = self.url(details)
            await self.git(root, 'clone', '--depth', '1', '--no-tags', '--single-branch',
                '--branch', details['branch'], '--', url, str(checkout), token=self.credential(details['agent_id']))
            commit = (await self.git(checkout, 'rev-parse', 'HEAD')).strip()
            if not re.fullmatch(r'[a-f0-9]{40}', commit):
                raise CoordinationError('cloud_commit', 'Git did not return a commit ID')
            baseline = root / 'baseline'
            source_revision = await asyncio.to_thread(snapshot, checkout, baseline)
            probe = root / 'check'
            await asyncio.to_thread(snapshot, baseline, probe)
            cell = await self.build(agent_id='cloud-check-' + identity[:12],
                workspace_root=self.projects.state, workspace=probe, backend='docker', image=image,
                policy=CellPolicy(allow_network=False, max_output_bytes=12000))
            try:
                outcome = await cell.run(details['check'], timeout=120)
            finally:
                try:
                    await cell.close()
                except Exception as exc:
                    raise CellCleanupError('Environment check Cell teardown could not be confirmed') from exc
            details = {**details, 'commit': commit, 'image': image, 'source_revision': source_revision,
                'check_result': {'exit_code': outcome.exit_code, 'timed_out': outcome.timed_out,
                    'output': (outcome.stdout + '\n' + outcome.stderr)[:12000]}}
            if outcome.exit_code != 0 or outcome.timed_out:
                await self.settle(identity, 'failed', details)
                return
            if (await asyncio.to_thread(inventory, baseline))[0] != source_revision:
                raise CoordinationError('cloud_source_changed', 'Prepared source changed during verification')
            await self.settle(identity, 'ready', {**details, 'review_hash': digest(details)})
        except asyncio.CancelledError:
            await asyncio.shield(self.settle(identity, 'reconciling', details))
            raise
        except Exception as exc:
            # Keep the check sandbox path and Git evidence for inspection.
            status = 'reconciling' if type(exc).__name__ == 'CellCleanupError' else 'failed'
            await self.settle(identity, status, {**details, 'error': self.safe_error(exc)})

    async def publish(self, owner, agent, project_id, body):
        native_id = await self.workspace().resolve(owner, agent, project_id, new_chat=True)
        async with write_session(self.sessions) as db:
            row = await db.get(ProjectOperation, body.environment_id, with_for_update=True)
            if (not row or row.project_id != native_id or row.kind != 'cloud_environment'
                    or row.details.get('agent_id') != self.workspace().agent(agent)):
                raise CoordinationError('not_found', 'Cloud environment unavailable', 404)
            if row.details.get('review_hash') != body.review_hash or row.status not in {'ready', 'published'}:
                raise CoordinationError('cloud_review_changed', 'Review a prepared environment before publishing', 409)
            if row.status == 'ready':
                await self.source({'id': row.id, **row.details})
                row.status = 'published'
                row.details = {**row.details, 'published_at': datetime.now(timezone.utc).isoformat()}
            return self.view(row)

    async def source(self, binding):
        from app.services.projects import inventory
        path = self.root(binding['id']) / 'baseline'
        try:
            revision = (await asyncio.to_thread(inventory, path))[0]
        except OSError as exc:
            raise CoordinationError('cloud_source_missing', 'Published source is unavailable on the execution host', 409) from exc
        if revision != binding['source_revision']:
            raise CoordinationError('cloud_source_changed', 'Published source changed; prepare a new environment revision', 409)
        return path

    async def reconcile_pull_request(self, owner, agent, project_id, operation_id):
        native_id = await self.workspace().resolve(owner, agent, project_id)
        async with self.sessions() as db:
            row = await db.get(ProjectOperation, operation_id)
        if (not row or row.project_id != native_id or row.kind != 'cloud_pr'
                or row.details.get('agent_id') != self.workspace().agent(agent)):
            raise CoordinationError('not_found', 'Cloud pull request unavailable', 404)
        if row.status == 'complete':
            return self.view(row)
        details = row.details
        if row.status != 'reconciling' or not details.get('commit'):
            raise CoordinationError('cloud_pr_not_ready', 'This pull request has no uncertain remote result', 409)
        token = self.credential(agent)
        if not token:
            raise CoordinationError('cloud_github_access', 'Configure a repository-scoped GitHub publishing credential', 503)
        environment = details['environment']
        repo = environment['repository']
        rows, _ = await self.github_api('GET', '/repos/' + repo + '/pulls', token,
            params={'state': 'all', 'head': repo.split('/')[0] + ':' + details['head'], 'base': environment['branch']})
        match = next((item for item in rows if item.get('head', {}).get('sha') == details['commit']
            and isinstance(item.get('number'), int)), None) if isinstance(rows, list) else None
        if match is None:
            raise CoordinationError('cloud_pr_uncertain', 'GitHub has no verified pull request for the recorded commit', 409)
        settled = {**details, 'phase': 'published', 'url': f"https://github.com/{repo}/pull/{match['number']}"}
        settled.pop('error', None)
        await self.settle(operation_id, 'complete', settled)
        return {'id': operation_id, 'status': 'complete', **settled}

    async def pull_request(self, owner, agent, project_id, body):
        workspace = self.workspace()
        native_id = await workspace.resolve(owner, agent, project_id)
        agent = workspace.agent(agent)
        identity = uuid5(NAMESPACE_URL, f'speda:cloud-pr:{owner}:{native_id}:{body.task_id}').hex
        request_hash = digest(body.model_dump())
        async with self.sessions() as db:
            prior = await db.get(ProjectOperation, identity)
        if prior:
            if prior.details['request_hash'] != request_hash or prior.details['agent_id'] != agent:
                raise CoordinationError('cloud_pr_conflict', 'This task already has a pull request operation', 409)
            return self.view(prior)
        token = self.credential(agent)
        if not token:
            raise CoordinationError('cloud_github_access', 'Configure a repository-scoped GitHub publishing credential', 503)
        review = await workspace.changes(owner, agent, project_id, body.task_id)
        if (not review['changes'] or body.expected_source_revision != review['expected_source_revision']
                or body.expected_artifact_revision != review['expected_artifact_revision']):
            raise CoordinationError('cloud_review_changed', 'Review the exact changed files before opening a pull request', 409)
        async with write_session(self.sessions) as db:
            prior = await db.get(ProjectOperation, identity)
            if prior:
                if prior.details['request_hash'] != request_hash or prior.details['agent_id'] != agent:
                    raise CoordinationError('cloud_pr_conflict', 'This task already has a pull request operation', 409)
                return self.view(prior)
            task = await db.get(ProjectTask, body.task_id)
            environment = (task.result or {}).get('cloud_environment')
            if not environment:
                raise CoordinationError('cloud_task_required', 'Choose a task from a published GitHub environment', 422)
            details = {'agent_id': agent, 'project_id': project_id, 'environment': environment,
                'request_hash': request_hash, 'task_id': body.task_id, 'title': body.title, 'body': body.body,
                'head': 'speda/' + body.task_id, 'source_revision': body.expected_source_revision,
                'artifact_revision': body.expected_artifact_revision}
            db.add(ProjectOperation(id=identity, project_id=native_id, kind='cloud_pr',
                status='executing', details=details))
        self.launch(self.publish_pull_request(identity, details, token))
        return {'id': identity, 'status': 'executing', **details}

    async def publish_pull_request(self, identity, details, token):
        from app.services.project_lifecycle import apply_snapshot
        try:
            environment = details['environment']
            await self.source(environment)
            root = self.root(identity)
            root.mkdir(parents=True, exist_ok=False)
            checkout = root / 'publish'
            source = self.root(environment['id']) / 'repository'
            # A new trusted Git directory. Workers never mount this repository.
            await self.git(root, '-c', 'protocol.file.allow=always', 'clone', '--no-hardlinks',
                '--no-checkout', '--', str(source), str(checkout))
            await self.git(checkout, 'checkout', '--detach', environment['commit'])
            artifact = self.projects.state / 'artifacts' / details['task_id']
            await asyncio.to_thread(apply_snapshot, artifact, checkout,
                details['artifact_revision'], details['source_revision'])
            await self.git(checkout, 'add', '--all', '--force', '--', '.')
            await self.git(checkout, '-c', 'user.name=Optimus', '-c', 'user.email=opt-import@users.noreply.github.com',
                '-c', 'commit.gpgsign=false', 'commit', '-m', details['title'])
            commit = (await self.git(checkout, 'rev-parse', 'HEAD')).strip()
            details = {**details, 'commit': commit, 'phase': 'pushing'}
            await self.settle(identity, 'executing', details)
            await self.git(checkout, 'push', '--', self.url(environment), 'HEAD:refs/heads/' + details['head'], token=token)
            details = {**details, 'phase': 'opening_pull_request'}
            await self.settle(identity, 'executing', details)
            result, error = await self.github_api('POST', '/repos/' + environment['repository'] + '/pulls', token,
                json={'title': details['title'], 'body': details['body'], 'head': details['head'],
                    'base': environment['branch'], 'draft': True})
            if not result or not isinstance(result.get('number'), int):
                raise CoordinationError('cloud_pr_uncertain', (error or 'GitHub did not return a pull request ID').replace(token, '[redacted]'))
            await self.settle(identity, 'complete', {**details, 'phase': 'published',
                'url': f"https://github.com/{environment['repository']}/pull/{result['number']}"})
        except asyncio.CancelledError:
            await asyncio.shield(self.settle(identity, 'reconciling', details))
            raise
        except Exception as exc:
            status = 'reconciling' if details.get('phase') else 'failed'
            await self.settle(identity, status, {**details, 'error': self.safe_error(exc).replace(token, '[redacted]')})

    @staticmethod
    def url(details):
        return 'https://github.com/' + details['repository'] + '.git'

    @staticmethod
    def safe_error(exc):
        return str(exc)[:2000] if isinstance(exc, CoordinationError) else type(exc).__name__

    async def git(self, directory, *args, token=None):
        config = [*_HARDEN, '-c', 'http.followRedirects=false', '-c', 'credential.helper=',
            '-c', 'init.templateDir=', '-c', 'core.autocrlf=false']
        if token:
            config += ['-c', 'credential.helper=' + _CRED_HELPER]
        return await self.command('git', '-C', str(directory), *config, *args, token=token)

    @staticmethod
    async def command(*args, token=None):
        env = {key: os.environ[key] for key in ('PATH', 'HOME', 'SystemRoot', 'USERPROFILE', 'TEMP', 'TMP',
            'DOCKER_HOST', 'DOCKER_API_VERSION') if key in os.environ}
        env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull, GIT_TERMINAL_PROMPT='0')
        if token:
            env[_TOKEN_ENV] = token
        try:
            process = await asyncio.create_subprocess_exec(*args, env=env, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        except FileNotFoundError as exc:
            raise CoordinationError('cloud_tool_missing', f'{args[0]} is unavailable on the execution host', 503) from exc
        preview = bytearray()

        async def drain():
            while chunk := await process.stdout.read(8192):
                preview.extend(chunk[:max(0, 16000-len(preview))])
            return await process.wait()

        try:
            code = await asyncio.wait_for(drain(), 150)
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        result = preview.decode('utf-8', errors='replace')
        if token:
            result = result.replace(token, '[redacted]')
        if code:
            raise CoordinationError('cloud_command_failed', result[-2000:].strip() or f'{args[0]} exited {code}')
        return result
