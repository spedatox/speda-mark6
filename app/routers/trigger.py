import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

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
    Zero business logic â€” constructs AgentContext and calls orchestrator.run().
    """
    validate_n8n_secret(request)
    if agent_id.casefold() == "orion" and not body.payload.get('automation_key'):
        # Legacy nightly triggers cannot bypass Iacon's queue and spending gate.
        from app.routers.iacon import context as maintenance_context
        ctx = maintenance_context()
        await request.app.state.orion.drain(ctx)
        await request.app.state.iacon_telegram.send_due(ctx)
        return TriggerResponse(accepted=True, request_id=ctx.request_id)

    return await request.app.state.automation_admissions.start(1, agent_id, body)


@router.get('/trigger/runs/{request_id}')
async def inspect_trigger(request: Request, request_id: str):
    return await request.app.state.automation_admissions.inspect(1, request_id)


@router.post('/trigger/pending/drain')
async def drain_pending_triggers(request: Request):
    validate_n8n_secret(request)
    return await request.app.state.automation_admissions.drain(1)
