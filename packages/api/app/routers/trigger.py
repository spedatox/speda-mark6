import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.context import AgentContext
from app.database import AsyncSessionLocal, get_db
from app.schemas.sse import SSEEventType
from app.schemas.trigger import TriggerRequest, TriggerResponse
from app.services.n8n import format_trigger_context, validate_n8n_secret

logger = logging.getLogger(__name__)
router = APIRouter(tags=["trigger"])


def _trigger_seed(payload: dict, output_mode: str) -> str:
    """The single user turn that kicks off an automated run.

    An automation fires with no human in the loop, so the seed must push the
    agent to EXECUTE its stored workflow with real tools — not to narrate or
    fabricate. The old wording ("compose the message the owner should see") let
    weaker models write a plausible-looking briefing without ever calling Gmail,
    the calendar, news_headlines or system_info. This says the opposite,
    explicitly, and tells the agent how its output is delivered so it doesn't
    double-send via send_telegram_message on a push.
    """
    intent = payload.get("intent") or ""
    delivery = {
        "respond": "Your reply streams straight back to the owner.",
        "push": (
            "Whatever you write as your reply IS delivered to the owner as a push "
            "notification — so do NOT also call send_telegram_message; that would "
            "double-send. Your composed text is the delivery."
        ),
        "silent": (
            "This is a silent run: your reply is stored, not shown to anyone right "
            "now. Still do the real work; keep the write-up brief."
        ),
    }.get(output_mode, "")
    return (
        "AUTOMATED TRIGGER — no human is waiting on this turn, so you must ACT, "
        "not narrate.\n\n"
        "The `intent` below is a workflow you wrote earlier for your future self. "
        "Execute it now, step by step, with your real tools:\n"
        "- Actually CALL each tool the intent implies. If a tool isn't loaded yet "
        "(Gmail, Calendar, Notion, …), load it with use_toolset first, then call "
        "it. news_headlines and system_info are always available.\n"
        "- Build every part of your message ONLY from what the tools actually "
        "return. If a tool errors or a section has nothing, SAY SO plainly — "
        "'no new important mail', 'calendar unavailable'. Never invent mail, "
        "events, headlines, or numbers. Fabricated data is a failure, not a "
        "fallback.\n"
        f"- {delivery}\n\n"
        f"intent: {intent}\n\n"
        f"full payload: {payload}"
    )


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
    telegram_bots = request.app.state.telegram_bots

    # Resolve the addressed agent. n8n targets a specific agent by path; an
    # unknown agent_id is a routing error, not a silent fallback.
    profile = request.app.state.profiles.get(agent_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Unknown agent '{agent_id}'")

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
        agent_id=agent_id,
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
                "content": _trigger_seed(body.payload, body.output_mode),
            }
        ],
        db=db,  # replaced with a task-owned session in _run_trigger
        timezone=settings.owner_timezone,
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
    asyncio.create_task(_run_trigger(orchestrator, telegram_bots, context, body.payload))

    return TriggerResponse(accepted=True, request_id=request_id)


async def _run_trigger(orchestrator, telegram_bots, context: AgentContext, payload: dict) -> None:
    """Run the orchestrator loop for a trigger request and deliver the result.

    Owns its DB session: the request-scoped session closes the moment the
    HTTP response returns, so this task must not touch it. push → the firing
    agent's OWN Telegram bot (fallback chain: own bot → SPEDA tagged → DB row);
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
                # The sender bot is derived from the AgentContext, never passed by
                # n8n — a Sentinel push speaks from Sentinel's bot. If every bot is
                # unreachable, persist a Notification row so nothing is lost.
                delivered = await telegram_bots.deliver_message(context.agent_id, final_text)
                if not delivered:
                    await _store_notification(db, context, final_text, payload)
                logger.info(
                    "trigger_push_delivered" if delivered else "trigger_push_stored",
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


async def _store_notification(db, context: AgentContext, text: str, payload: dict) -> None:
    """Fallback when no Telegram bot could deliver (unconfigured / unlinked):
    persist the push as a Notification row so the desktop app surfaces it on next
    open. Best-effort — a storage failure must not crash the task."""
    try:
        from app.models.notification import Notification

        title = str(payload.get("event") or payload.get("job") or "Update")[:255]
        db.add(
            Notification(
                user_id=context.user_id,
                source_agent=context.agent_id,
                triggered_by="n8n",
                title=title,
                body=text,
                priority=str(payload.get("priority", "normal")),
                delivered=False,
            )
        )
        await db.commit()
    except Exception as e:  # noqa: BLE001
        logger.error(
            "trigger_notification_store_failed",
            extra={"request_id": context.request_id, "error": str(e)},
        )
