from fastapi import APIRouter, Request

from app.schemas.cloud_projects import CloudPullRequest, PrepareCloudEnvironment, PrepareGitHubWorkspace, PublishCloudEnvironment

router = APIRouter(prefix='/projects', tags=['cloud-projects'])


@router.post('/cloud/github-workspaces', status_code=202)
async def create_github_workspace(request: Request, body: PrepareGitHubWorkspace):
    return await request.app.state.projects.cloud.prepare_managed(1, body)


@router.post('/cloud/environments', status_code=202)
async def create(request: Request, body: PrepareCloudEnvironment):
    return await request.app.state.projects.cloud.prepare(1, body)


@router.get('/{project_id}/cloud')
async def status(request: Request, project_id: int, agent_id: str = 'optimus'):
    return await request.app.state.projects.cloud.status(1, agent_id, project_id)


@router.post('/{project_id}/cloud/environments', status_code=202)
async def prepare(request: Request, project_id: int, body: PrepareCloudEnvironment):
    return await request.app.state.projects.cloud.prepare(1, body, project_id=project_id)


@router.post('/{project_id}/cloud/publish')
async def publish(request: Request, project_id: int, body: PublishCloudEnvironment, agent_id: str = 'optimus'):
    return await request.app.state.projects.cloud.publish(1, agent_id, project_id, body)


@router.post('/{project_id}/cloud/pull-requests', status_code=202)
async def open_pull_request(request: Request, project_id: int, body: CloudPullRequest, agent_id: str = 'optimus'):
    return await request.app.state.projects.cloud.pull_request(1, agent_id, project_id, body)


@router.post('/{project_id}/cloud/pull-requests/{operation_id}/reconcile')
async def reconcile(request: Request, project_id: int, operation_id: str, agent_id: str = 'optimus'):
    return await request.app.state.projects.cloud.reconcile_pull_request(1, agent_id, project_id, operation_id)


@router.post('/{project_id}/cloud/environments/{operation_id}/reconcile', status_code=202)
async def reconcile_environment(request: Request, project_id: int, operation_id: str, agent_id: str = 'optimus'):
    return await request.app.state.projects.cloud.reconcile_environment(1, agent_id, project_id, operation_id)


@router.post('/{project_id}/cloud/checkpoints/{operation_id}/reconcile')
async def reconcile_checkpoint(request: Request, project_id: int, operation_id: str, agent_id: str = 'optimus'):
    return await request.app.state.projects.cloud.reconcile_checkpoint(1, agent_id, project_id, operation_id)
