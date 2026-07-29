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
import uuid
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

    # A finished background legionnaire needs the OPPOSITE instruction to a
    # briefing. The block above pushes hard on "execute the workflow with real
    # tools", which is right when the work hasn't happened yet and is exactly
    # wrong here: the work is done, the findings are in the payload, and an
    # agent told to ACT will re-run the searches it already paid for — or
    # redeploy the worker and loop. This branch says: read, synthesise, report.
    if payload.get("type") == "legion_report":
        status = payload.get("status") or "ok"
        failed = status != "ok"
        return (
            "BACKGROUND WORK COMPLETE — a legionnaire you deployed earlier has "
            "finished and the owner has not seen the result yet. No human is "
            "waiting in this turn; your job is to deliver the finding.\n\n"
            "- The work is ALREADY DONE. Do NOT re-run the research, do NOT "
            "repeat the worker's tool calls, and do NOT deploy another "
            "legionnaire. Everything you need is below.\n"
            "- Write the owner's message FROM the findings. Lead with the answer, "
            "not with preamble about the worker. One short line naming which "
            "legionnaire ran and what it was asked, then the substance.\n"
            + (
                "- This run FAILED. Say so plainly, give the error, and say what "
                "you would try next. Do NOT invent a result to fill the gap.\n"
                if failed else
                "- Keep it to what the findings actually support. If they are "
                "thin or inconclusive, say that rather than padding.\n"
            )
            + f"- {delivery}\n\n"
            f"legionnaire: {payload.get('worker') or 'unknown'}\n"
            f"task: {payload.get('task') or '(unspecified)'}\n"
            f"status: {status}\n"
            f"ticket: {payload.get('ticket') or '(untracked)'}\n\n"
            "findings:\n"
            f"{payload.get('result') or '(the worker returned nothing)'}"
        )

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
        # A legion report is not n8n's doing — the agent's own worker woke it.
        # The UI labels the sender from this, and attributing it to the
        # automation channel would misreport who started the conversation.
        "source": "legion" if payload.get("type") == "legion_report" else "n8n",
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
    triggered_by: str = "n8n",
) -> tuple[str | None, int]:
    """Launch an automated turn as a detached, persisted chat turn.

    Returns (request_id | None, session_id) — None means the turn registry was
    at capacity and nothing was started, which the router reports to n8n so it
    can retry instead of assuming the job ran.

    `triggered_by` defaults to "n8n" because that is who fires almost all of
    these. A completed background legionnaire reporting back is genuinely
    "agent" — the same three values AgentContext has always allowed — and
    labelling it honestly keeps the session list and the logs truthful about
    what woke the agent up.
    """
    agent_id = profile.agent_id
    model = profile.allocate_model(triggered_by)

    session = await session_manager.get_or_create(
        db=db,
        user_id=user_id,
        triggered_by=triggered_by,
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
        triggered_by=triggered_by,
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


# ── Legion completion reports ────────────────────────────────────────────────


def make_legion_reporter(
    *,
    profiles,
    orchestrator,
    turns,
    session_manager,
    telegram_bots,
    agent_proxy=None,
    ws_manager=None,
    user_id: int = 1,
):
    """Build the callback a finished BACKGROUND legionnaire fires to report in.

    A background worker used to end in silence: the result landed in an
    agent_messages ticket and sat there until the owner happened to ask
    legion_status. For a job the owner deliberately sent away — "go research
    this, tell me when you're done" — silence is the one outcome that makes the
    feature useless. This turns completion into a real turn on the agent that
    deployed the worker, with output_mode="push", so the agent reads the
    findings and writes the owner's message and the existing
    push → Telegram → notification chain delivers it.

    Deliberately a callback built here rather than logic inside LegionRunner:
    the Legion owns worker execution and must not learn about sessions,
    delivery or the turn registry (Rule 1). This closes over what the lifespan
    already assembled and hands the Legion one awaitable.

    Inline workers are NOT reported — their result returns into the parent turn,
    where the agent is already holding it and already replying. Reporting those
    would double-send.
    """

    async def report(
        *,
        agent_id: str,
        worker_id: str,
        task: str,
        result: str,
        status: str,
        ticket: int | None = None,
    ) -> None:
        profile = profiles.get(agent_id)
        if profile is None:
            logger.warning(
                "legion_report_unknown_agent", extra={"agent_id": agent_id}
            )
            return

        payload = {
            "type": "legion_report",
            "job": f"{worker_id} report",
            "worker": worker_id,
            "task": task,
            "result": result,
            "status": status,
            "ticket": ticket,
        }
        request_id = str(uuid.uuid4())

        # Its own DB session: the turn that deployed this worker finished long
        # ago and its session is gone.
        async with AsyncSessionLocal() as db:
            started, session_id = await start_trigger_turn(
                db=db,
                profile=profile,
                payload=payload,
                output_mode="push",
                request_id=request_id,
                orchestrator=orchestrator,
                turns=turns,
                session_manager=session_manager,
                telegram_bots=telegram_bots,
                agent_proxy=agent_proxy,
                ws_manager=ws_manager,
                user_id=user_id,
                triggered_by="agent",
            )
        logger.info(
            "legion_report_started" if started else "legion_report_refused",
            extra={
                "request_id": request_id,
                "agent_id": agent_id,
                "worker": worker_id,
                "status": status,
                "ticket": ticket,
                "session_id": session_id,
            },
        )

    return report
