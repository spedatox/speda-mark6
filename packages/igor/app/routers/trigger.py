import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.trigger_runner import start_trigger_turn
from app.database import get_db
from app.schemas.trigger import TriggerRequest, TriggerResponse
from app.services.n8n import validate_n8n_secret

logger = logging.getLogger(__name__)
router = APIRouter(tags=["trigger"])


@router.post("/trigger/{agent_id}", response_model=TriggerResponse)
async def trigger(
    agent_id: str,
    request: Request,
    body: TriggerRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    n8n webhook trigger endpoint.
    Authenticated by both X-API-Key (middleware) and X-N8N-Secret (this handler).
    Zero business logic — resolves the agent and hands off to the trigger runner,
    which launches the turn on the same detached machinery chat uses (Rule 1).
    """
    validate_n8n_secret(request)

    # Resolve the addressed agent. n8n targets a specific agent by path; an
    # unknown agent_id is a routing error, not a silent fallback.
    profile = request.app.state.profiles.get(agent_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Unknown agent '{agent_id}'")

    request_id = str(uuid.uuid4())
    started, session_id = await start_trigger_turn(
        db=db,
        profile=profile,
        payload=body.payload,
        output_mode=body.output_mode,
        request_id=request_id,
        orchestrator=request.app.state.orchestrator,
        turns=request.app.state.turns,
        session_manager=request.app.state.session_manager,
        telegram_bots=request.app.state.telegram_bots,
        agent_proxy=request.app.state.agent_proxy,
        ws_manager=request.app.state.ws_manager,
    )

    logger.info(
        "trigger_received" if started else "trigger_refused_busy",
        extra={
            "request_id": request_id,
            "agent_id": agent_id,
            "session_id": session_id,
            "output_mode": body.output_mode,
            "payload_type": body.payload.get("type", "unknown"),
        },
    )
    if started is None:
        # Turn registry at capacity — tell n8n plainly so it retries rather than
        # recording a job that never ran.
        raise HTTPException(
            status_code=503,
            detail="Too many turns are running at once — retry shortly.",
        )

    return TriggerResponse(accepted=True, request_id=request_id)
