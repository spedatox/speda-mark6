"""Original UI contracts routed into the renovated services."""
from fastapi import APIRouter, Request, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from uuid import uuid4
from app.schemas.client_compat import ClientChat
from app.services.coordination_budget import CoordinationError

router = APIRouter(tags=["client-compatibility"])


@router.get("/chat/active")
async def active(request: Request, session_id: int | None = None, agent_id: str | None = None):
    return await request.app.state.client_compat.active(1, session_id, agent_id)


@router.get("/chat/attach/{request_id}")
async def attach(request: Request, request_id: str):
    adapter = request.app.state.client_compat
    _, run = await adapter.resolve(1, request_id)
    if run is None:
        raise CoordinationError("admission_pending", "Resend the original request to finish admission", 409)
    return StreamingResponse(adapter.stream(1, request_id,
        structured=request.headers.get('X-Chat-Format') == 'room-v1'), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/chat/cancel/{request_id}")
async def cancel(request: Request, request_id: str):
    return await request.app.state.client_compat.cancel(1, request_id)


class Steering(BaseModel):
    text: str = Field(min_length=1, max_length=6000)
    message_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1, max_length=64, pattern=r'^[\w-]+$')
    expected_step_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')


@router.post("/chat/steer/{request_id}")
async def steer(request: Request, request_id: str, body: Steering):
    from app.services.mission_control import MissionControl
    from app.schemas.coordination import SupervisorSteering
    _, run = await request.app.state.client_compat.resolve(1, request_id)
    if run is None:
        return {'steered': False, 'reason': 'Conversation admission is still pending'}
    return await MissionControl(request.app.state.coordinator).steer(1, run.id,
        SupervisorSteering(input_id=body.message_id, message=body.text, expected_step_id=body.expected_step_id))


@router.post("/chat/{agent_id}")
async def chat(request: Request, agent_id: str, body: ClientChat):
    result = await request.app.state.client_compat.start(1, agent_id, body,
        structured=request.headers.get('X-Chat-Format') == 'room-v1')
    return await attach(request, result["request_id"])


@router.get("/sessions")
async def sessions(request: Request, agent_id: str = "speda", limit: int = Query(default=500, ge=1, le=500),
                   before: int | None = Query(default=None, ge=1)):
    return await request.app.state.client_compat.list_sessions(1, agent_id, limit, before)


@router.get("/sessions/{session_id}/messages")
async def messages(request: Request, session_id: int):
    return await request.app.state.client_compat.messages(1, session_id,
        structured=request.headers.get('X-Chat-Format') == 'room-v1')


@router.get('/sessions/{session_id}/room')
async def room(request: Request, session_id: int, agent_id: str | None = None):
    return await request.app.state.client_compat.room_for_session(1, session_id, agent_id)


@router.get('/sessions/{session_id}/export')
async def export(request: Request, session_id: int):
    return await request.app.state.client_compat.export(1, session_id)


class Rename(BaseModel):
    title: str = Field(min_length=1, max_length=255)


@router.patch("/sessions/{session_id}")
async def rename(request: Request, session_id: int, body: Rename):
    return await request.app.state.client_compat.edit_session(1, session_id, title=body.title)


@router.delete("/sessions/{session_id}")
async def delete(request: Request, session_id: int):
    return await request.app.state.client_compat.edit_session(1, session_id, hide=True)
