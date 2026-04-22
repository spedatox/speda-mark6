import logging

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.agent import AgentRegistration, AgentStatus
from fastapi import Depends

logger = logging.getLogger(__name__)
router = APIRouter(tags=["agents"])


@router.get("/agents", response_model=list[AgentStatus])
async def list_agents(request: Request):
    """Return the list of currently online Superior Six agents."""
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
