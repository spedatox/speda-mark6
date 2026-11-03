import logging

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.agent import AgentRegistration, AgentStatus
from fastapi import Depends
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(tags=["agents"])


@router.get('/agents/comms')
async def agent_comms(request:Request,limit:int=100,after_id:int=0,session_id:int|None=None):
    from app.services.comms import comms
    return await comms(request.app.state.coordinator.sessions,1,limit,max(0,after_id),session_id)


@router.get('/agents/asks')
async def pending_asks(request: Request):
    from app.services.project_release import ProjectRelease
    projects = getattr(request.app.state, 'projects', None)
    return await ProjectRelease(projects).pending(1) if projects else []


class ReleaseAnswer(BaseModel):
    approved: bool
    remember: bool = False


@router.post('/agents/asks/{ask_id}')
async def retired_peer_answer(request: Request, ask_id: str, body: ReleaseAnswer):
    from app.services.project_release import ProjectRelease
    from app.services.coordination_budget import CoordinationError
    projects = getattr(request.app.state, 'projects', None)
    if projects is None:
        raise CoordinationError('peer_retired', 'Peer permission expansion is retired', 410)
    return await ProjectRelease(projects).answer(1, ask_id, approved=body.approved, remember=body.remember)


@router.get("/agents", response_model=list[AgentStatus])
async def list_agents(request: Request):
    """Return the list of currently online Superior Six agents."""
    if hasattr(request.app.state, 'coordinator'):
        return [AgentStatus(agent_id=p['agent_id'], agent_name=p['name'], domain=p['domain'],
            status='online' if not request.app.state.coordinator.closing else 'offline',
            last_seen=None, capabilities=p['tools']) for p in request.app.state.profiles.roster()]
    agent_registry = request.app.state.agent_registry
    return agent_registry.list_online()


@router.websocket("/agents/ws/{agent_id}")
async def agent_websocket(
    agent_id: str,
    websocket: WebSocket,
    db: AsyncSession = Depends(get_db),
):
    """
    WebSocket endpoint for Superior Six agent connections.
    Agents send a registration handshake on connect, then receive task dispatches.
    This is NOT the Flutter user WebSocket — see /ws for that.
    """
    import hmac
    from app.config import settings
    if (not hmac.compare_digest(websocket.headers.get('x-api-key', ''), settings.speda_api_key)
            or hasattr(websocket.app.state, 'coordinator')):
        # Retired remote-agent dispatch must not compete with Coordinator.
        await websocket.close(code=1008)
        return
    agent_registry = websocket.app.state.agent_registry

    try:
        # Wait for registration message
        data = await websocket.receive_json()
        registration = AgentRegistration(**data)

        await agent_registry.register(websocket, registration, db)

        # Keep connection alive and handle incoming messages
        while True:
            message = await websocket.receive_json()
            msg_type = message.get("type")

            if msg_type == "heartbeat":
                await websocket.send_json({"type": "acknowledge", "agent_id": agent_id})
            elif msg_type == "task_result":
                logger.info(
                    "agent_task_result",
                    extra={"agent_id": agent_id, "task_id": message.get("task_id")},
                )
            else:
                logger.warning(
                    "agent_unknown_message",
                    extra={"agent_id": agent_id, "type": msg_type},
                )

    except WebSocketDisconnect:
        await agent_registry.deregister(agent_id, db)
    except Exception as e:
        logger.error("agent_ws_error", extra={"agent_id": agent_id, "error": str(e)})
        await agent_registry.deregister(agent_id, db)
