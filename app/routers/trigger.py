import logging
import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import AgentContext
from app.database import get_db
from app.schemas.trigger import TriggerRequest, TriggerResponse
from app.services.n8n import format_trigger_context, validate_n8n_secret

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
    Zero business logic — constructs AgentContext and calls orchestrator.run().
    """
    validate_n8n_secret(request)

    orchestrator = request.app.state.orchestrator
    session_manager = request.app.state.session_manager
    profile = request.app.state.profile

    request_id = str(uuid.uuid4())
    user_id = 1

    session = await session_manager.get_or_create(
        db=db,
        user_id=user_id,
        triggered_by="n8n",
        model_used=profile.allocate_model("n8n"),
        agent_id=agent_id,
    )

    context = AgentContext(
        user_id=user_id,
        session_id=session.id,
        request_id=request_id,
        triggered_by="n8n",
        trigger_payload=format_trigger_context(body.payload),
        output_mode=body.output_mode,
        model=profile.allocate_model("n8n"),
        system_prompt="",
        conversation_history=[
            {
                "role": "user",
                "content": f"Automated trigger received: {body.payload}",
            }
        ],
        db=db,
        timezone="UTC",
    )

    logger.info(
        "trigger_received",
        extra={
            "request_id": request_id,
            "agent_id": agent_id,
            "output_mode": body.output_mode,
            "payload_type": body.payload.get("type", "unknown"),
        },
    )

    # Run the orchestrator as a background task
    # For push/silent modes we don't stream — fire and forget
    import asyncio

    asyncio.create_task(_run_trigger(orchestrator, context))

    return TriggerResponse(accepted=True, request_id=request_id)


async def _run_trigger(orchestrator, context: AgentContext) -> None:
    """Run the orchestrator loop for a trigger request, consuming all SSE events."""
    try:
        async for event in orchestrator.run(context):
            # Events are consumed internally for push/silent modes
            # TODO: For push mode, call NotificationsSkill when DONE event arrives
            pass
    except Exception as e:
        logger.error(
            "trigger_run_error",
            extra={"request_id": context.request_id, "error": str(e)},
        )
