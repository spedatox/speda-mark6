import asyncio
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.models.coordination import CoordinationRun, ProjectTask
from app.schemas.client_compat import ClientChat
from app.schemas.cloud_projects import CloudPullRequest, PrepareCloudEnvironment, PrepareGitHubWorkspace, PublishCloudEnvironment
from app.services.cloud_projects import CloudEnvironments
from app.services.coordination_budget import CoordinationError
from app.schemas.llm import Block, LLMMessage
from app.skills.collaboration import StartGitHubProjectSkill
from forge.runtime import ExecutionResult
from forge.tools.gitpush import GitHubHTTPError
from tests.test_coordination import reply
from tests.test_project_workspace import chat, fixture
from tests.test_projects import delegate


class LocalGitCloud(CloudEnvironments):
    """Real Git commits, checkouts and pushes to a disposable bare repository."""

    def __init__(self, projects, remote, github_api, checks):
        async def build(**kwargs):
            assert kwargs['backend'] == 'docker' and not kwargs['policy'].allow_network
            assert kwargs['image'] == 'sha256:' + 'a' * 64
            assert (kwargs['workspace'] / 'example.txt').read_text() == 'cloud source'

            async def run(command, timeout):
                checks.append(command)
                return SimpleNamespace(exit_code=0, timed_out=False, stdout='Toolchain check passed', stderr='')

            async def close():
                checks.append('closed')

            return SimpleNamespace(run=run, close=close)

        super().__init__(projects, build=build, github_api=github_api)
        self.remote = remote

    @staticmethod
    def url(details):
        return 'https://github.com/example/repository.git'

    def credential(self, agent):
        return 'fixture-publisher'

    async def command(self, *args, token=None):
        if args[0] == 'docker':
            return 'sha256:' + 'a' * 64
        mapped = tuple(str(self.remote) if arg == self.url({}) else arg for arg in args)
        return await super().command(*mapped, token=None)


async def setup_cloud(memory, tmp_path, script=()):
    bits = await fixture(memory, tmp_path, script)
    bits.projects.settings.project_cell_image = 'fixture-toolchain'
    bits.projects.settings.github_token = 'fixture-publisher'
    remote = tmp_path / 'remote.git'
    seed = tmp_path / 'seed'
    seed.mkdir()
    calls, checks = [], []

    async def github(method, path, token, **kwargs):
        assert token == 'fixture-publisher' and path == '/repos/example/repository/pulls'
        calls.append((method, kwargs))
        return {'number': 17}, ''

    cloud = LocalGitCloud(bits.projects, remote, github, checks)
    bits.projects.cloud = cloud
    await cloud.git(tmp_path, 'init', '--bare', '--initial-branch=main', str(remote))
    await cloud.git(seed, 'init', '--initial-branch=main')
    (seed / 'example.txt').write_text('cloud source')
    await cloud.git(seed, 'add', '--', '.')
    await cloud.git(seed, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test',
        'commit', '-m', 'Existing project')
    await cloud.git(seed, 'push', '--', str(remote), 'HEAD:refs/heads/main')
    return bits, cloud, calls, checks


async def settle_jobs(cloud):
    await asyncio.wait_for(asyncio.gather(*cloud.jobs), 20)


async def test_cloud_prepare_publish_continue_and_draft_pr(memory, tmp_path):
    bits, cloud, calls, checks = await setup_cloud(memory, tmp_path, [delegate(), reply('Saved cloud artifact')])
    body = PrepareCloudEnvironment(request_id=uuid4().hex, name='Cloud application',
        repository='https://github.com/Example/Repository', check='git --version')
    draft = await cloud.prepare(1, body)
    assert draft['status'] == 'preparing'
    assert (await cloud.prepare(1, body))['id'] == draft['id']
    await settle_jobs(cloud)
    project_id = draft['project_id']
    assert (await bits.workspace.get(1, 'optimus', project_id))['cloud']
    ready = (await cloud.status(1, 'optimus', project_id))['environments'][0]
    assert ready['status'] == 'ready' and checks == ['git --version', 'closed']
    with pytest.raises(CoordinationError, match='Publish'):
        await chat(bits, project_id=project_id)
    with pytest.raises(CoordinationError, match='Review'):
        await cloud.publish(1, 'optimus', project_id, PublishCloudEnvironment(
            environment_id=ready['id'], review_hash='b' * 64))
    published = await cloud.publish(1, 'optimus', project_id, PublishCloudEnvironment(
        environment_id=ready['id'], review_hash=ready['review_hash']))
    assert published['status'] == 'published'
    first = await chat(bits, project_id=project_id)
    async with memory.sessions() as db:
        first_task = await db.scalar(select(ProjectTask).where(ProjectTask.run_id == first['run_id']))
        run = await db.get(CoordinationRun, first['run_id'])
    assert run.policy['cloud_environment']['commit'] == ready['commit']
    assert first_task.result['cloud_environment']['image'] == ready['image']
    assert (await bits.workspace.state(1, 'optimus', project_id))['files'] == ['example.txt']
    bits.client.script.extend([delegate(dependencies=[first_task.id]), reply('Continued the saved task')])
    second = await chat(bits, project_id=project_id)
    async with memory.sessions() as db:
        task = await db.scalar(select(ProjectTask).where(ProjectTask.run_id == second['run_id']))
    assert task.base_revision == first_task.result['revision'] and task.status == 'succeeded'
    review = await bits.workspace.changes(1, 'optimus', project_id, task.id)
    assert '-cloud source' in review['diff'] and '+implemented once' in review['diff']
    assert (await cloud.git(tmp_path, '--git-dir=' + str(cloud.remote), 'rev-parse', 'main')).strip() == ready['commit']
    pr_body = CloudPullRequest(task_id=task.id, expected_source_revision=review['expected_source_revision'],
        expected_artifact_revision=review['expected_artifact_revision'], title='Review the saved change')
    pr = await cloud.pull_request(1, 'optimus', project_id, pr_body)
    await settle_jobs(cloud)
    assert (await cloud.pull_request(1, 'optimus', project_id, pr_body))['id'] == pr['id']
    status = (await cloud.status(1, 'optimus', project_id))['pull_requests'][0]
    assert status['status'] == 'complete' and status['url'].endswith('/pull/17')
    assert len(calls) == 1 and calls[0][1]['json']['draft'] is True
    published_content = await cloud.git(tmp_path, '--git-dir=' + str(cloud.remote),
        'show', 'refs/heads/' + status['head'] + ':example.txt')
    assert published_content == 'implemented once'
    assert (await cloud.git(tmp_path, '--git-dir=' + str(cloud.remote), 'rev-parse', 'main')).strip() == ready['commit']
    await bits.coordinator.shutdown()


async def test_environment_hash_and_repository_scope_are_enforced(memory, tmp_path):
    bits, cloud, _, _ = await setup_cloud(memory, tmp_path)
    for repo in ('https://user:token@github.com/example/repository', 'file:///tmp/repo', 'example/../repo'):
        with pytest.raises(ValueError):
            PrepareCloudEnvironment(request_id=uuid4().hex, name='Bad', repository=repo)
    body = PrepareCloudEnvironment(request_id=uuid4().hex, name='Cloud application', repository='example/repository')
    draft = await cloud.prepare(1, body)
    await settle_jobs(cloud)
    with pytest.raises(CoordinationError, match='another environment'):
        await cloud.prepare(1, body.model_copy(update={'branch': 'other'}))
    ready = (await cloud.status(1, 'optimus', draft['project_id']))['environments'][0]
    (cloud.root(ready['id']) / 'baseline' / 'example.txt').write_text('tampered')
    with pytest.raises(CoordinationError, match='source changed'):
        await cloud.publish(1, 'optimus', draft['project_id'], PublishCloudEnvironment(
            environment_id=ready['id'], review_hash=ready['review_hash']))
    with pytest.raises(CoordinationError):
        await cloud.status(2, 'optimus', draft['project_id'])
    await bits.coordinator.shutdown()


class ManagedGitCloud(CloudEnvironments):
    def __init__(self, projects, tmp_path):
        super().__init__(projects, build=self.cell, github_api=self.api)
        self.tmp_path = tmp_path
        self.repos = {}
        self.calls = []
        self.lose_create_response = False

    async def cell(self, **kwargs):
        assert kwargs['backend'] == 'docker' and not kwargs['policy'].allow_network

        async def run(command, timeout):
            assert command == 'git --version'
            return SimpleNamespace(exit_code=0, timed_out=False, stdout='git available', stderr='')

        async def close():
            pass

        return SimpleNamespace(run=run, close=close)

    def remote(self, repository):
        return self.tmp_path / (repository.replace('/', '-') + '.git')

    async def command(self, *args, token=None):
        if args[0] == 'docker':
            return 'sha256:' + 'a' * 64
        mapped = [str(self.remote(arg.removeprefix('https://github.com/').removesuffix('.git')))
            if arg.startswith('https://github.com/') else arg for arg in args]
        return await super().command(*mapped, token=None)

    async def seed(self, name, content):
        directory = self.tmp_path / (name.replace('/', '-') + '-seed')
        directory.mkdir()
        await self.git(directory, 'init', '--initial-branch=main')
        (directory / 'example.txt').write_text(content)
        await self.git(directory, 'add', '.')
        await self.git(directory, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test',
            'commit', '-m', 'Seed')
        await self.git(directory, 'clone', '--bare', '--', str(directory), str(self.remote(name)))
        return directory

    async def api(self, method, path, token, **kwargs):
        assert token == 'managed-fixture-token'
        self.calls.append((method, path, kwargs))
        if path == '/user':
            return {'login': 'opt-fixture', 'id': 21}, ''
        if method == 'GET':
            repo = self.repos.get(path.removeprefix('/repos/'))
            return (repo, '') if repo else (None, GitHubHTTPError(404, 'Missing'))
        if path == '/user/repos':
            body = kwargs['json']
            assert body['private'] is True and body['auto_init'] is True
            name = 'opt-fixture/' + body['name']
            await self.seed(name, 'prototype seed')
            self.repos[name] = {'full_name': name, 'owner': {'id': 21}, 'id': 22, 'private': True,
                'default_branch': 'main', 'description': body['description'], 'fork': False}
            if self.lose_create_response:
                return None, 'Connection lost after create'
            return self.repos[name], ''
        if path.endswith('/forks'):
            upstream = self.repos[path.removeprefix('/repos/').removesuffix('/forks')]
            name = 'opt-fixture/' + kwargs['json']['name']
            await self.git(self.tmp_path, 'clone', '--bare', '--',
                str(self.remote(upstream['full_name'])), str(self.remote(name)))
            self.repos[name] = {'full_name': name, 'owner': {'id': 21}, 'id': 25,
                'private': upstream['private'], 'default_branch': 'main', 'fork': True, 'parent': upstream}
            return self.repos[name], ''
        if '/collaborators/' in path:
            assert method == 'PUT' and path.endswith('/owner-fixture')
            return {'id': 24}, ''
        if path == '/repos/example/upstream/pulls':
            head = kwargs['json']['head'].split(':')[1]
            sha = await self.git(self.tmp_path, '--git-dir=' + str(self.remote('opt-fixture/upstream')),
                'rev-parse', 'refs/heads/' + head)
            return {'number': 28, 'head': {'sha': sha.strip(), 'repo': {'id': 25}}}, ''
        raise AssertionError(path)


async def setup_managed(memory, tmp_path, script=()):
    bits = await fixture(memory, tmp_path, script)
    settings = bits.projects.settings
    settings.project_cell_image = 'fixture-toolchain'
    settings.optimus_github_account = 'opt-fixture'
    settings.optimus_github_commit_email = '21+opt-fixture@users.noreply.github.com'
    settings.optimus_github_workspace_token = SecretStr('managed-fixture-token')
    settings.optimus_github_share_with = 'owner-fixture'
    cloud = ManagedGitCloud(bits.projects, tmp_path)
    bits.projects.cloud = cloud
    return bits, cloud


async def test_managed_prototype_starts_from_first_task_and_continues_checkpoint(memory, tmp_path):
    bits, cloud = await setup_managed(memory, tmp_path, [delegate(), reply('Built the prototype')])
    body = PrepareGitHubWorkspace(request_id=uuid4().hex, name='Prototype', mode='prototype', task='Build me a prototype')
    created = await cloud.prepare_managed(1, body)
    await settle_jobs(cloud)
    state = await cloud.status(1, 'optimus', created['project_id'])
    env = state['environments'][0]
    assert env['status'] == 'published' and env['share_status'] == 'sent', env
    assert env['target']['private'] is True and not env.get('task_error'), env
    if run := bits.coordinator.live.get(env['initial_run_id']):
        await run
    await settle_jobs(cloud)
    state = await cloud.status(1, 'optimus', created['project_id'])
    first = state['checkpoints'][0]
    assert first['status'] == 'complete', first
    assert first['url'] == env['target']['url'] + '/tree/' + first['commit']
    remote = '--git-dir=' + str(cloud.remote(env['repository']))
    author = await cloud.git(tmp_path, remote, 'show', '-s', '--format=%ae', first['commit'])
    assert author.strip() == '21+opt-fixture@users.noreply.github.com'
    assert (await cloud.prepare_managed(1, body))['id'] == created['id']
    assert len([call for call in cloud.calls if call[:2] == ('POST', '/user/repos')]) == 1

    async def follow_up(spec, **kwargs):
        assert (spec.workspace / 'example.txt').read_text() == 'implemented once'
        (spec.workspace / 'continued.txt').write_text('continued from the checkpoint')
        return ExecutionResult(spec.job_id, 'succeeded', 'Extended prototype')

    bits.projects.executor = follow_up
    bits.client.script.extend([delegate(), reply('Extended the prototype')])
    await chat(bits, project_id=created['project_id'])
    await settle_jobs(cloud)
    latest = (await cloud.status(1, 'optimus', created['project_id']))['checkpoints'][0]
    assert latest['status'] == 'complete', latest
    parent = await cloud.git(tmp_path, remote, 'rev-parse', latest['commit'] + '^')
    assert parent.strip() == first['commit']
    await bits.coordinator.shutdown()


async def test_managed_fork_needs_public_consent_and_pushes_only_to_fork(memory, tmp_path):
    bits, cloud = await setup_managed(memory, tmp_path, [delegate(), reply('Built the change')])
    await cloud.seed('example/upstream', 'upstream source')
    cloud.repos['example/upstream'] = {'full_name': 'example/upstream', 'id': 23,
        'private': False, 'default_branch': 'main'}
    body = PrepareGitHubWorkspace(request_id=uuid4().hex, name='Fork', mode='fork', repository='example/upstream')
    blocked = await cloud.prepare_managed(1, body)
    await settle_jobs(cloud)
    assert (await cloud.status(1, 'optimus', blocked['project_id']))['environments'][0]['status'] == 'failed'
    assert not any(call[0] == 'POST' for call in cloud.calls)

    created = await cloud.prepare_managed(1, body.model_copy(update={'request_id': uuid4().hex, 'allow_public': True}))
    await settle_jobs(cloud)
    env = (await cloud.status(1, 'optimus', created['project_id']))['environments'][0]
    assert env['status'] == 'published' and env['repository'] == 'opt-fixture/upstream', env
    reused = await cloud.prepare_managed(1, body.model_copy(update={'request_id': uuid4().hex, 'allow_public': True}))
    await settle_jobs(cloud)
    assert (await cloud.status(1, 'optimus', reused['project_id']))['environments'][0]['target']['id'] == env['target']['id']
    assert len([call for call in cloud.calls if call[:2] == ('POST', '/repos/example/upstream/forks')]) == 1

    await chat(bits, project_id=created['project_id'])
    await settle_jobs(cloud)
    checkpoint = (await cloud.status(1, 'optimus', created['project_id']))['checkpoints'][0]
    assert checkpoint['status'] == 'complete', checkpoint
    source = '--git-dir=' + str(cloud.remote('example/upstream'))
    assert (await cloud.git(tmp_path, source, 'for-each-ref', '--format=%(refname)')).strip() == 'refs/heads/main'
    review = await bits.workspace.changes(1, 'optimus', created['project_id'], checkpoint['task_id'])
    await cloud.pull_request(1, 'optimus', created['project_id'], CloudPullRequest(
        task_id=checkpoint['task_id'], title='Review checkpoint',
        expected_source_revision=review['expected_source_revision'],
        expected_artifact_revision=review['expected_artifact_revision']))
    await settle_jobs(cloud)
    pr = (await cloud.status(1, 'optimus', created['project_id']))['pull_requests'][0]
    assert pr['url'] == 'https://github.com/example/upstream/pull/28'
    post = next(kwargs['json'] for method, path, kwargs in cloud.calls if method == 'POST' and path.endswith('/pulls'))
    assert post['head'] == 'opt-fixture:' + checkpoint['head'] and post['draft'] is True
    await bits.coordinator.shutdown()


async def test_managed_creation_reconciles_lost_response_without_replaying_post(memory, tmp_path):
    bits, cloud = await setup_managed(memory, tmp_path)
    cloud.lose_create_response = True
    created = await cloud.prepare_managed(1, PrepareGitHubWorkspace(request_id=uuid4().hex,
        name='Interrupted prototype', mode='prototype'))
    await settle_jobs(cloud)
    pending = (await cloud.status(1, 'optimus', created['project_id']))['environments'][0]
    assert pending['status'] == 'reconciling' and pending['phase'] == 'repository_requested'
    await cloud.reconcile_environment(1, 'optimus', created['project_id'], created['id'])
    await settle_jobs(cloud)
    final = (await cloud.status(1, 'optimus', created['project_id']))['environments'][0]
    assert final['status'] == 'published', final
    assert len([call for call in cloud.calls if call[:2] == ('POST', '/user/repos')]) == 1
    await bits.coordinator.shutdown()


async def test_direct_optimus_chat_starts_managed_project_with_owner_prompt(memory, tmp_path):
    request = LLMMessage([Block(type='tool_use', id='github1', name='start_github_project',
        input={'name': 'Prototype from chat', 'mode': 'prototype'})], 'tool_use')
    bits, cloud = await setup_managed(memory, tmp_path, [request, reply('Queued the new project'),
        delegate(), reply('Built the new project')])
    await bits.engine._registry.register_skill(StartGitHubProjectSkill())
    message = 'Prototype me a local log viewer.'
    turn = await bits.adapter.start(1, 'optimus', ClientChat(message=message))
    if run := bits.coordinator.live.get(turn['run_id']):
        await run
    await settle_jobs(cloud)
    project = next(project for project in await bits.workspace.listing(1, 'optimus') if project['cloud'])
    env = (await cloud.status(1, 'optimus', project['id']))['environments'][0]
    assert env['initial_task'] == message and env.get('initial_run_id'), env
    if run := bits.coordinator.live.get(env['initial_run_id']):
        await run
    await settle_jobs(cloud)
    assert (await cloud.status(1, 'optimus', project['id']))['checkpoints'][0]['status'] == 'complete'
    await bits.coordinator.shutdown()
