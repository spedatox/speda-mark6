"""Authenticated project/sidebar contracts; all behavior belongs to services."""
from fastapi import APIRouter, Request, Query
from pydantic import BaseModel, Field
from app.schemas.project_workspace import WorkspaceCreate, WorkspacePatch, WorkspaceDirectory
from app.schemas.client_compat import DocumentAttachment
from app.schemas.coordination import IntegrateProject
from app.services.project_workspace import ProjectWorkspace

router = APIRouter(prefix='/projects', tags=['project-workspaces'])


def workspace(request):
    return ProjectWorkspace(request.app.state.coordinator)


@router.get('/folders')
async def folders(request: Request, path: str = Query(default='', max_length=2000)):
    return await workspace(request).folders(path)


@router.post('/folders', status_code=201)
async def mkdir(request: Request, body: WorkspaceDirectory):
    return await workspace(request).mkdir(body)


@router.get('')
async def projects(request: Request, agent_id: str = 'optimus', include_archived: bool = False):
    return await workspace(request).listing(1, agent_id, include_archived)


@router.post('', status_code=201)
async def create(request: Request, body: WorkspaceCreate):
    return await workspace(request).create(1, body)


@router.get('/{project_id}')
async def project(request: Request, project_id: int, agent_id: str = 'optimus'):
    return await workspace(request).get(1, agent_id, project_id)


@router.patch('/{project_id}')
async def patch(request: Request, project_id: int, body: WorkspacePatch, agent_id: str = 'optimus'):
    return await workspace(request).patch(1, agent_id, project_id, body)


@router.delete('/{project_id}')
async def remove(request: Request, project_id: int, agent_id: str = 'optimus'):
    return await workspace(request).remove(1, agent_id, project_id)


@router.get('/{project_id}/sessions')
async def sessions(request: Request, project_id: int, agent_id: str = 'optimus'):
    return await workspace(request).sessions_for(1, agent_id, project_id)


@router.get('/{project_id}/files')
async def files(request: Request, project_id: int, agent_id: str = 'optimus'):
    return await workspace(request).files(1, agent_id, project_id)


@router.post('/{project_id}/files', status_code=201)
async def upload(request: Request, project_id: int, body: DocumentAttachment, agent_id: str = 'optimus'):
    return await workspace(request).upload(1, agent_id, project_id, body)


@router.delete('/{project_id}/files/{file_id}')
async def remove_file(request: Request, project_id: int, file_id: int, agent_id: str = 'optimus'):
    return await workspace(request).remove_file(1, agent_id, project_id, file_id)


@router.get('/{project_id}/workspace')
async def state(request: Request, project_id: int, agent_id: str = 'optimus'):
    return await workspace(request).state(1, agent_id, project_id)


@router.get('/{project_id}/workspace/file')
async def read_file(request: Request, project_id: int, path: str = Query(max_length=2000), agent_id: str = 'optimus'):
    from types import SimpleNamespace
    service = workspace(request)
    native_id = await service.resolve(1, agent_id, project_id)
    return await service.read(SimpleNamespace(user_id=1, agent_id=service.agent(agent_id), project_id=native_id), path)


@router.get('/{project_id}/changes/{task_id}')
async def changes(request: Request, project_id: int, task_id: str, agent_id: str = 'optimus'):
    return await workspace(request).changes(1, agent_id, project_id, task_id)


@router.post('/{project_id}/integrate')
async def integrate(request: Request, project_id: int, body: IntegrateProject, agent_id: str = 'optimus'):
    return await workspace(request).integrate(1, agent_id, project_id, body)


class Rollback(BaseModel):
    expected_revision: str = Field(pattern=r'^[a-f0-9]{64}$')


@router.post('/{project_id}/operations/{operation_id}/rollback')
async def rollback(request: Request, project_id: int, operation_id: str, body: Rollback, agent_id: str = 'optimus'):
    return await workspace(request).rollback(1, agent_id, project_id, operation_id, body.expected_revision)
