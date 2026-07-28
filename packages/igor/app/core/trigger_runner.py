"""
Automated (n8n) turn runner — an automated turn IS a chat turn.

Rule 1 keeps the trigger router logic-free; this module is where that logic
lives. It exists because the two paths had silently drifted apart: a chat turn
was persisted, stamped, titled, indexed and detached, while a triggered turn
was an ad-hoc loop inside the router that streamed into a list and threw
everything away. The session row was still created, so the owner saw a "New
conversation" in the sidebar that opened empty — the work happened, nothing
survived it — and the next run started blind because no history existed.

So a triggered run now goes through exactly the machinery a chat turn does:

  seed → saved as a real user message → load_history (timestamp-stamped from
  created_at, same as chat) → AgentContext → TurnRegistry.start(...) → the
  assistant turn (with its tool calls and files) is persisted by the runner →
  post-turn tasks (session log, recap, compaction, embeddings) → delivery.

Consequences that are the whole point: the transcript is readable in the app,
`/chat/attach/{request_id}` can tail a briefing live, `/chat/cancel` stops one,
a crashed run still leaves its partial work behind, and the next turn in that
session sees what the last one did.

Delivery (push → Telegram → Notification row) hangs off the settle hook rather
than the success hook, so a run that errors half-way still delivers what it
produced — the pre-existing behaviour, kept deliberately.
"""

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.clock import owner_now
from app.core.context import AgentContext
from app.database import AsyncSessionLocal
from app.models.message import Message

logger = logging.getLogger(__name__)


def format_trigger_context(payload: dict) -> dict:
    """
    Normalise an n8n trigger payload into a standard context dict.
    Adds defaults for fields n8n may omit.
    """
    return {
        "type": payload.get("type", "unknown"),
        "job": payload.get("job"),
        "from_agent": payload.get("from"),
        "event": payload.get("event"),
        "raw": payload,
    }


def build_seed(payload: dict, output_mode: str) -> str:
    """The single user turn that kicks off an automated run.

    An automation fires with no human in the loop, so the seed must push the
    agent to EXECUTE its stored workflow with real tools — not to narrate or
    fabricate. The old wording ("compose the message the owner should see") let
    weaker models write a plausible-looking briefing without ever calling Gmail,
    the calendar, news_headlines or system_info. This says the opposite,
    explicitly, and tells the agent how its output is delivered so it doesn't
    double-send via send_telegram_message on a push.

    Returned RAW, with no timestamp: the caller persists it and reads it back
    through load_history, which stamps it from its stored created_at exactly
    like a chat message. That stamp is how any agent knows the current date —
    the system prompt deliberately carries no clock (it would churn the cached
    prefix every minute), and an unstamped automated turn left agents date-blind,
    querying date-scoped tools against whatever "today" their training suggested.
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


def trigger_meta(payload: dict, output_mode: str) -> dict:
    """Display-only provenance for the seed turn, carried in the `_speda_meta`
    block the UI already knows how to recover on reload.

    A triggered run is a normal conversation in the app — but the owner did not
    write its opening message, and a bubble attributed to them would be a lie
    the next reader has no way to catch. This is what lets the UI label the
    sender as the automation instead. `_clean` strips the block before the
    history goes back to the model, so it costs nothing in the prompt.
    """
    return {
        "source": "n8n",
        "label": _label(payload),
        "job": payload.get("job") or payload.get("event") or payload.get("type") or "",
        "automation": payload.get("automation") or "",
        "output_mode": output_mode,
    }


def _label(payload: dict) -> str:
    raw = (
        payload.get("automation")
        or payload.get("job")
        or payload.get("event")
        or payload.get("type")
        or "Automated run"
    )
    label = str(raw).replace("_", " ").replace("-", " ").strip() or "Automated run"
    return f"{label[:1].upper()}{label[1:]}"


def session_title(payload: dict, today: datetime | None = None) -> str:
    """Name the session from the automation itself, at launch.

    Set up front rather than left to generate_title: the owner should be able to
    tell this morning's brief from yesterday's in the sidebar the moment it
    fires, and a model-written title derived from the seed would just paraphrase
    the "AUTOMATED TRIGGER" boilerplate every time. generate_title is idempotent
    on an already-titled session, so it steps aside.
    """
    # The owner's date, not the container's: a run at 01:00 Istanbul is still
    # "yesterday" in UTC, and the sidebar would name it the wrong day.
    stamp = (today or owner_now()).strftime("%d %b")
    return f"{_label(payload)} · {stamp}"[:255]


async def start_trigger_turn(
    *,
    db: AsyncSession,
    profile,
    payload: dict,
    output_mode: str,
    request_id: str,
    orchestrator,
    turns,
    session_manager,
    telegram_bots,
    agent_proxy=None,
    ws_manager=None,
    user_id: int = 1,
) -> tuple[str | None, int]:
    """Launch an automated turn as a detached, persisted chat turn.

    Returns (request_id | None, session_id) — None means the turn registry was
    at capacity and nothing was started, which the router reports to n8n so it
    can retry instead of assuming the job ran.
    """
    agent_id = profile.agent_id
    model = profile.allocate_model("n8n")

    session = await session_manager.get_or_create(
        db=db,
        user_id=user_id,
        triggered_by="n8n",
        model_used=model,
        agent_id=agent_id,
    )
    if session.title is None:
        session.title = session_title(payload)
        await db.commit()

    # Persist FIRST, then load history back — the same order chat uses, so the
    # seed is stamped from its own created_at and the transcript the owner opens
    # is byte-identical to the prompt the model saw.
    await session_manager.save_message(
        db,
        session.id,
        "user",
        [
            {"type": "text", "text": build_seed(payload, output_mode)},
            {"type": "_speda_meta", "trigger": trigger_meta(payload, output_mode)},
        ],
    )
    history = await session_manager.load_history(db, session.id)

    context = AgentContext(
        agent_id=agent_id,
        user_id=user_id,
        session_id=session.id,
        request_id=request_id,
        triggered_by="n8n",
        trigger_payload=format_trigger_context(payload),
        output_mode=output_mode,
        model=model,
        system_prompt="",
        conversation_history=history,
        db=db,  # replaced with the runner's own session before the engine runs
        timezone=settings.owner_timezone,
    )
    # Toolsets loaded in an earlier turn of this session stay loaded, exactly as
    # in chat — otherwise every run re-calls use_toolset and rewrites the cache.
    context.extra["active_servers"] = session_manager.get_loaded_servers(session.id)

    bg_model = profile.background_model(model)

    async def on_complete() -> None:
        from app.services.memory import run_post_turn_tasks

        await run_post_turn_tasks(session.id, request_id, user_id, bg_model)

    async def on_settle(status: str) -> None:
        await _deliver(
            agent_id=agent_id,
            session_id=session.id,
            request_id=request_id,
            user_id=user_id,
            output_mode=output_mode,
            payload=payload,
            telegram_bots=telegram_bots,
            status=status,
        )

    # Engine selection, identical to chat: an agent whose real backend is a
    # connected standalone peer (Optimus/Forge) runs THERE. Without this a
    # triggered turn would quietly fall back to the in-process stub while chat
    # turns to the same agent reached the peer.
    use_external = bool(
        getattr(profile, "external_backend", False)
        and agent_proxy is not None
        and ws_manager is not None
        and ws_manager.is_connected(agent_id)
    )

    started = turns.start(
        context=context,
        engine_factory=(
            (lambda ctx: agent_proxy.run(ctx)) if use_external
            else (lambda ctx: orchestrator.run(ctx))
        ),
        format_error=lambda exc: f"Automated run failed: {exc}",
        on_complete=on_complete,
        on_settle=on_settle,
    )
    return started, session.id


async def _deliver(
    *,
    agent_id: str,
    session_id: int,
    request_id: str,
    user_id: int,
    output_mode: str,
    payload: dict,
    telegram_bots,
    status: str,
) -> None:
    """Post-run delivery: stamp the automation, then push if asked.

    The text comes back out of the DB rather than off a stream, so what the
    owner is sent is exactly what the transcript shows — including the runner's
    marker when a turn broke off early.
    """
    async with AsyncSessionLocal() as db:
        automation_name = payload.get("automation")
        if automation_name:
            from app.automations.manager import mark_fired

            await mark_fired(str(automation_name), db)

        if output_mode != "push":
            return

        text = await _last_assistant_text(db, session_id)
        if not text:
            logger.warning(
                "trigger_push_empty",
                extra={"request_id": request_id, "status": status},
            )
            return

        # The sender bot is derived from the agent, never passed by n8n — a
        # Sentinel push speaks from Sentinel's bot. If every bot is unreachable,
        # persist a Notification row so nothing is lost.
        delivered = await telegram_bots.deliver_message(agent_id, text)
        if not delivered:
            await _store_notification(db, agent_id, user_id, request_id, text, payload)
        logger.info(
            "trigger_push_delivered" if delivered else "trigger_push_stored",
            extra={"request_id": request_id, "chars": len(text), "status": status},
        )


async def _last_assistant_text(db: AsyncSession, session_id: int) -> str:
    """The text of the newest assistant message in the session, flattened out of
    its content blocks (tool/file meta blocks carry no text and are skipped)."""
    row = (
        await db.execute(
            select(Message)
            .where(Message.session_id == session_id, Message.role == "assistant")
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(1)
        )
    ).scalars().first()
    if row is None:
        return ""
    content = row.content
    if isinstance(content, str):
        return content.strip()
    parts = [
        b.get("text", "")
        for b in (content or [])
        if isinstance(b, dict) and b.get("type") == "text"
    ]
    return "".join(parts).strip()


async def _store_notification(
    db: AsyncSession, agent_id: str, user_id: int, request_id: str,
    text: str, payload: dict,
) -> None:
    """Fallback when no Telegram bot could deliver (unconfigured / unlinked):
    persist the push as a Notification row so the desktop app surfaces it on next
    open. Best-effort — a storage failure must not crash the task."""
    try:
        from app.models.notification import Notification

        title = str(payload.get("event") or payload.get("job") or "Update")[:255]
        db.add(
            Notification(
                user_id=user_id,
                source_agent=agent_id,
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
            extra={"request_id": request_id, "error": str(e)},
        )
