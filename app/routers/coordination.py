"""Authenticated owner endpoints. All state transitions belong to the coordinator."""
from fastapi import APIRouter, Request, Query, UploadFile
from fastapi.responses import StreamingResponse
from app.schemas.coordination import (CreateRoom, RoomInput, Membership, PersonaUpdate,
    SpendingUpdate, CreateProject, Checkpoint, Decision)

router = APIRouter(prefix="/coordination", tags=["coordination"])
from app.services.global_budget import GlobalUpdate
from app.schemas.coordination import Steering, SupervisorSteering
from app.schemas.coordination import RelocateProject, IntegrateProject, Contract
from pydantic import Field
from app.services.project_lifecycle import ProjectLifecycle
from app.services.room_attachments import Attachments
from app.schemas.coordination import ApproveRelease, ReconcileRelease
from app.services.project_release import ProjectRelease


@router.get('/releases/{operation_id}')
async def release_plan(request: Request, operation_id: str):
    return await ProjectRelease(request.app.state.projects).inspect(1, operation_id)


@router.post('/releases/{operation_id}/decision')
async def decide_release(request: Request, operation_id: str, body: ApproveRelease):
    return await ProjectRelease(request.app.state.projects).decide(1, operation_id, body)


@router.post('/releases/{operation_id}/reconcile')
async def reconcile_release(request: Request, operation_id: str, body: ReconcileRelease):
    return await ProjectRelease(request.app.state.projects).reconcile(1, operation_id, body)


@router.post("/rooms/{room_id}/attachments")
async def attachment(request: Request, room_id: str, file: UploadFile):
    return await Attachments(request.app.state.coordinator).upload(1, room_id, file)


class Rollback(Contract):
    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")


@router.post("/projects/{project_id}/relocate")
async def relocate_project(request: Request, project_id: str, body: RelocateProject):
    return await ProjectLifecycle(request.app.state.projects).relocate(1, project_id, body)


@router.post("/projects/{project_id}/integrate")
async def integrate_project(request: Request, project_id: str, body: IntegrateProject):
    return await ProjectLifecycle(request.app.state.projects).integrate(1, project_id, body)


@router.post("/project-operations/{operation_id}/rollback")
async def rollback_project(request: Request, operation_id: str, body: Rollback):
    return await ProjectLifecycle(request.app.state.projects).rollback(1, operation_id, body.expected_revision)


@router.post("/tasks/{task_id}/input")
async def steer_task(request: Request, task_id: str, body: Steering):
    return await request.app.state.projects.steer(1, task_id, body)


@router.post('/runs/{run_id}/input')
async def steer_supervisor(request: Request, run_id: str, body: SupervisorSteering):
    from app.services.mission_control import MissionControl
    return await MissionControl(request.app.state.coordinator).steer(1, run_id, body)


@router.get("/global-budget")
async def global_budget(request: Request):
    return await request.app.state.global_budget.status(1)


@router.put("/global-budget")
async def global_budget_update(request: Request, body: GlobalUpdate):
    return await request.app.state.global_budget.save(1, body.expected_version, body.policy)


@router.get("/personas")
async def personas(request: Request):
    return request.app.state.profiles.roster()


@router.get("/personas/{agent_id}")
async def persona(request: Request, agent_id: str):
    return await request.app.state.coordinator.preference(1, agent_id)


@router.put("/personas/{agent_id}")
async def preference(request: Request, agent_id: str, body: PersonaUpdate):
    return await request.app.state.coordinator.preference(1, agent_id, body)


@router.get("/budget")
async def budget(request: Request):
    return await request.app.state.coordination_budget.status(1)


@router.put("/budget")
async def update_budget(request: Request, body: SpendingUpdate):
    return await request.app.state.coordination_budget.save(1, body.expected_version, body.policy)


@router.get("/rooms")
async def rooms(request: Request):
    return await request.app.state.coordinator.rooms(1)


@router.get("/rooms/{room_id}/decisions")
async def decisions(request: Request, room_id: str):
    return await request.app.state.coordinator.decisions(1, room_id)


@router.post("/rooms/{room_id}/decisions")
async def decision(request: Request, room_id: str, body: Decision):
    return await request.app.state.coordinator.record_decision(1, room_id, body)


@router.post("/rooms", status_code=201)
async def create(request: Request, body: CreateRoom):
    return await request.app.state.coordinator.create(1, body)


@router.post("/rooms/{room_id}/members")
async def membership(request: Request, room_id: str, body: Membership):
    return await request.app.state.coordinator.membership(1, room_id, body)


@router.post("/rooms/{room_id}/messages", status_code=202)
async def message(request: Request, room_id: str, body: RoomInput):
    return await request.app.state.coordinator.start(1, room_id, body)


@router.get("/rooms/{room_id}/events")
async def events(request: Request, room_id: str, after: int = Query(default=0, ge=0)):
    return await request.app.state.coordinator.events(1, room_id, after)


@router.get("/rooms/{room_id}/stream")
async def stream(request: Request, room_id: str, after: int = Query(default=0, ge=0)):
    coordinator = request.app.state.coordinator
    await coordinator.events(1, room_id, after)  # Authorize before starting the response.
    return StreamingResponse(coordinator.subscribe(1, room_id, after), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/runs/{run_id}")
async def run(request: Request, run_id: str):
    return await request.app.state.coordinator.inspect_run(1, run_id)


@router.post("/runs/{run_id}/cancel")
async def cancel(request: Request, run_id: str):
    return await request.app.state.coordinator.cancel(1, run_id)


@router.post("/steps/{step_id}/cancel")
async def stop_reply(request: Request, step_id: str):
    return await request.app.state.coordinator.stop_reply(1, step_id)


@router.post("/runs/{run_id}/resume")
async def resume(request: Request, run_id: str):
    return await request.app.state.coordinator.resume(1, run_id)


@router.post("/recover")
async def recover(request: Request):
    return await request.app.state.coordinator.recover()


@router.post("/projects", status_code=201)
async def create_project(request: Request, body: CreateProject):
    return await request.app.state.projects.create(1, body)


@router.get("/projects/{project_id}")
async def project(request: Request, project_id: str):
    return await request.app.state.projects.inspect(1, project_id)


@router.put("/projects/{project_id}/checkpoint")
async def checkpoint(request: Request, project_id: str, body: Checkpoint):
    return await request.app.state.projects.checkpoint(1, project_id, body)


@router.post("/tasks/{task_id}/cancel")
async def stop_task(request: Request, task_id: str):
    return await request.app.state.projects.stop_task(1, task_id)
