"""Original Legion interfaces over native child state and project projections."""
from uuid import uuid4
from fastapi import APIRouter, Request, Query
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel, ConfigDict, Field

router=APIRouter(tags=['legion'])


class WorkerMessage(BaseModel):
    model_config=ConfigDict(extra='forbid')
    text: str=Field(min_length=1,max_length=6000)
    message_id: str=Field(default_factory=lambda:uuid4().hex,pattern='^[a-f0-9]{32}$')


@router.get('/legion/sessions/{session_id}/attach')
async def attach_session(request: Request, session_id: int, after: int = Query(default=0, ge=0)):
    service = request.app.state.worker_control
    room_id = await service.session_room(1, session_id)
    await service.coordinator.events(1, room_id, after)
    return StreamingResponse(service.subscribe_session(1, session_id, after), media_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@router.get('/legion/sessions/{session_id}/snapshot')
async def session_snapshot(request: Request, session_id: int):
    return await request.app.state.worker_control.session_snapshot(1, session_id)


@router.get('/legion/executions')
async def executions(request:Request,session_id:int|None=None,agent_id:str|None=None,limit:int=Query(default=50,ge=1,le=100)):
    return await request.app.state.worker_control.list(1,session_id,agent_id,limit)


@router.get('/legion/executions/{execution_id}')
async def execution(request:Request,execution_id:str):
    return await request.app.state.worker_control.inspect(1,execution_id)


@router.get('/legion/executions/{execution_id}/events')
async def events(request:Request,execution_id:str,after:int=Query(default=0,ge=0)):
    return await request.app.state.worker_control.events(1,execution_id,after)


@router.get('/legion/executions/{execution_id}/attach')
async def attach(request:Request,execution_id:str,after:int=Query(default=0,ge=0)):
    service=request.app.state.worker_control
    await service.events(1,execution_id,after)
    return StreamingResponse(service.subscribe(1,execution_id,after),media_type='text/event-stream',
        headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})


@router.post('/legion/executions/{execution_id}/interrupt')
async def interrupt(request:Request,execution_id:str):
    return await request.app.state.worker_control.interrupt(1,execution_id)


@router.post('/legion/executions/{execution_id}/messages')
async def message(request:Request,execution_id:str,body:WorkerMessage):
    return await request.app.state.worker_control.message(1,execution_id,body.text,body.message_id)


@router.get('/legion/executions/{execution_id}/event-artifacts/{digest}')
async def artifact(request:Request,execution_id:str,digest:str):
    return Response(await request.app.state.worker_control.artifact(1,execution_id,digest),media_type='application/json')


@router.get('/legion/active')
async def active(request:Request,session_id:int|None=None):
    return await request.app.state.worker_control.active(1,session_id)


@router.get('/legion/attach/{ticket}')
async def attach_ticket(request:Request,ticket:int,after:int=Query(default=0,ge=0)):
    service=request.app.state.worker_control
    execution_id=await service.resolve_ticket(1,ticket)
    await service.events(1,execution_id,after)
    return StreamingResponse(service.subscribe(1,execution_id,after),media_type='text/event-stream',
        headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})
