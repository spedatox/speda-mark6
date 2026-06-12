import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import AgentContext
from app.database import AsyncSessionLocal, get_db
from app.schemas.sse import SSEEventType
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
    telegram = request.app.state.telegram

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
                "content": (
                    "Automated trigger received. Compose the message the owner should "
                    "see — short, concrete, leading with what happened. The 'intent' "
                    f"field is your past instruction to yourself. Payload: {body.payload}"
                ),
            }
        ],
        db=db,  # replaced with a task-owned session in _run_trigger
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

    # Run the orchestrator as a background task — push/silent modes don't stream.
    asyncio.create_task(_run_trigger(orchestrator, telegram, context, body.payload))

    return TriggerResponse(accepted=True, request_id=request_id)


async def _run_trigger(orchestrator, telegram, context: AgentContext, payload: dict) -> None:
    """Run the orchestrator loop for a trigger request and deliver the result.

    Owns its DB session: the request-scoped session closes the moment the
    HTTP response returns, so this task must not touch it. push → Telegram;
    silent → stored in the session transcript only.
    """
    try:
        async with AsyncSessionLocal() as db:
            context.db = db

            chunks: list[str] = []
            async for event in orchestrator.run(context):
                if event.type == SSEEventType.CHUNK and isinstance(event.data, str):
                    chunks.append(event.data)
                elif event.type == SSEEventType.ERROR:
                    logger.error(
                        "trigger_orchestrator_error",
                        extra={"request_id": context.request_id, "error": str(event.data)},
                    )
            final_text = "".join(chunks).strip()

            # Stamp the automation's last-fired time (best-effort, by name).
            automation_name = payload.get("automation")
            if automation_name:
                from app.automations.manager import mark_fired

                await mark_fired(str(automation_name), db)

            if context.output_mode == "push" and final_text:
                delivered = await telegram.send_message(final_text)
                logger.info(
                    "trigger_push_delivered" if delivered else "trigger_push_failed",
                    extra={"request_id": context.request_id, "chars": len(final_text)},
                )
            elif context.output_mode == "push":
                logger.warning(
                    "trigger_push_empty",
                    extra={"request_id": context.request_id},
                )
    except Exception as e:  # noqa: BLE001
        logger.error(
            "trigger_run_error",
            extra={"request_id": context.request_id, "error": str(e)},
        )
