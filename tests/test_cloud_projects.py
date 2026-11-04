import asyncio
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.coordination import CoordinationRun, ProjectTask
from app.schemas.cloud_projects import CloudPullRequest, PrepareCloudEnvironment, PublishCloudEnvironment
from app.services.cloud_projects import CloudEnvironments
from app.services.coordination_budget import CoordinationError
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
