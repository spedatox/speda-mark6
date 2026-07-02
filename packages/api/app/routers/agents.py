import logging

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.runtime_state import (
    get_agent_models,
    get_house_party,
    set_agent_model,
    set_house_party,
)
from app.database import get_db
from app.models.agent_message import AgentMessage
from app.schemas.agent import (
    AgentCommEntry,
    AgentModelInfo,
    AgentModelSet,
    AgentRegistration,
    AgentStatus,
    HousePartyState,
)
from fastapi import Depends, HTTPException

logger = logging.getLogger(__name__)
router = APIRouter(tags=["agents"])


@router.get("/agents", response_model=list[AgentStatus])
async def list_agents(request: Request):
    """Return the list of currently online Superior Six agents."""
    agent_registry = request.app.state.agent_registry
    return agent_registry.list_online()


@router.get("/agents/comms", response_model=list[AgentCommEntry])
async def agent_comms(
    limit: int = 100,
    after_id: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """Recent inter-agent traffic (newest first) — feeds the comms tray in the
    UI. `after_id` lets the tray poll incrementally for new rows."""
    stmt = select(AgentMessage).order_by(AgentMessage.id.desc()).limit(min(limit, 300))
    if after_id:
        stmt = stmt.where(AgentMessage.id > after_id)
    rows = (await db.execute(stmt)).scalars().all()
    return list(rows)


@router.get("/agents/models", response_model=list[AgentModelInfo])
async def agent_models(request: Request):
    """Every in-process agent's model allocation: profile defaults + the owner's
    runtime pin, if any. Feeds the per-agent model selectors in the UI."""
    from app.config import settings

    overrides = get_agent_models()
    return [
        AgentModelInfo(
            agent_id=p.agent_id,
            name=p.name,
            domain=p.domain,
            override=overrides.get(p.agent_id),
            # What allocate_model would pick WITHOUT the owner's pin — the .env
            # deployment override, else the profile's own models.
            default_main=settings.llm_main_model or p.sonnet_model,
            default_background=settings.llm_background_model or p.haiku_model,
        )
        for p in request.app.state.profiles.roster()
        # Session-scope aliases (warroom) mirror their parent's brain — they are
        # not separate cores, so keep them out of the model selector lists.
        if p.dispatch_target
    ]


@router.post("/agents/models", response_model=list[AgentModelInfo])
async def agent_model_set(body: AgentModelSet, request: Request):
    """Pin an agent to a model ref (or clear the pin with model=null). The pin
    wins over the profile's allocation for interactive and dispatch runs."""
    if request.app.state.profiles.get(body.agent_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown agent '{body.agent_id}'")
    set_agent_model(body.agent_id, (body.model or "").strip() or None)
    return await agent_models(request)


@router.get("/agents/house-party", response_model=HousePartyState)
async def house_party_state():
    """Current House Party Protocol state."""
    return HousePartyState(engaged=get_house_party())


@router.post("/agents/house-party", response_model=HousePartyState)
async def house_party_toggle(body: HousePartyState):
    """Engage or stand down the House Party Protocol (owner-driven, from the UI)."""
    return HousePartyState(engaged=set_house_party(body.engaged))


@router.websocket("/agents/ws/{agent_id}")
async def agent_websocket(
    agent_id: str,
    websocket: WebSocket,
    db: AsyncSession = Depends(get_db),
):
    """
    WebSocket endpoint for external peer agent connections (Optimus).
    Peers send a registration handshake on connect, then receive task dispatches
    and proxied chat requests. This is NOT the Flutter user WebSocket — see /ws.
    """
    # AuthMiddleware only covers http-scope requests — WebSocket handshakes
    # bypass it entirely, so the API key MUST be checked here, before accept().
    import hmac

    from app.config import settings

    key = websocket.headers.get("x-api-key", "")
    if not (key and hmac.compare_digest(key, settings.speda_api_key)):
        await websocket.close(code=1008)  # policy violation
        logger.warning("agent_ws_auth_rejected", extra={"agent_id": agent_id})
        return

    agent_registry = websocket.app.state.agent_registry
    agent_proxy = websocket.app.state.agent_proxy

    await websocket.accept()

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
                agent_registry.touch(agent_id)
                await websocket.send_json({"type": "acknowledge", "agent_id": agent_id})
            elif msg_type == "task_result":
                # Correlate back to a waiting dispatch (app/core/dispatch.py).
                resolved = websocket.app.state.dispatcher.resolve_external_result(
                    str(message.get("task_id", "")),
                    str(message.get("result", "")),
                    status=str(message.get("status", "ok")),
                )
                logger.info(
                    "agent_task_result",
                    extra={
                        "agent_id": agent_id,
                        "task_id": message.get("task_id"),
                        "resolved": resolved,
                    },
                )
            elif msg_type == "chat_event":
                # Streamed frame of a proxied chat — route to its waiting
                # SSE stream (app/core/external_proxy.py).
                agent_proxy.deliver(
                    str(message.get("chat_id", "")), message.get("event") or {}
                )
            else:
                logger.warning(
                    "agent_unknown_message",
                    extra={"agent_id": agent_id, "type": msg_type},
                )

    except WebSocketDisconnect:
        agent_proxy.fail_agent(agent_id)
        await agent_registry.deregister(agent_id, db)
    except Exception as e:
        logger.error("agent_ws_error", extra={"agent_id": agent_id, "error": str(e)})
        agent_proxy.fail_agent(agent_id)
        await agent_registry.deregister(agent_id, db)
