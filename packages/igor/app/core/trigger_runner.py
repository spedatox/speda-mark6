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
import time
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.clock import owner_now
from app.core.context import AgentContext
from app.database import AsyncSessionLocal
from app.models.message import Message
from app.services.chat_history import final_answer_text

logger = logging.getLogger(__name__)

# How much of a worker's raw findings the report card carries for display. The
# reply is the answer; this is the receipt behind it, and a receipt nobody can
# scroll is no better than one nobody can open.
REPORT_DISPLAY_CHARS = 6000


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

    # A finished background worker — a legionnaire, or another agent you
    # dispatched to — needs the OPPOSITE instruction to a briefing. The block
    # above pushes hard on "execute the workflow with real tools", which is right
    # when the work hasn't happened yet and is exactly wrong here: the work is
    # done, the findings are in the payload, and an agent told to ACT will re-run
    # the research it already paid for — or redeploy the worker and loop. This
    # branch says: read, synthesise, report.
    if payload.get("type") in ("legion_report", "dispatch_report"):
        return _completion_seed(payload, delivery)

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
        "- Your reply IS the message, start to finish — no transition sentence "
        "about writing it first ('I have everything I need, let me compose "
        "the briefing', 'here's the summary:'). The owner never sees that "
        "line as a separate step; it just reads as the opening of the report, "
        "which makes no sense there.\n"
        f"- {delivery}\n\n"
        f"intent: {intent}\n\n"
        f"full payload: {payload}"
    )


def _completion_seed(payload: dict, delivery: str) -> str:
    """The seed for a finished piece of BACKGROUND work reporting back.

    One shape, two sources: a legionnaire the agent deployed (`legion_report`)
    and another agent it dispatched to in the background (`dispatch_report`).
    Only the nouns differ — the instruction is identical, because the failure
    mode is identical: an agent handed a finished result and no framing will
    either re-run the work or narrate the ticket instead of the answer.
    """
    legion = payload.get("type") == "legion_report"
    status = payload.get("status") or "ok"
    failed = status != "ok"
    # Resumed = this turn continues the very conversation the work was ordered
    # from, so the thread above is the agent's own. Everything the owner said
    # there — constraints, what they actually care about, what they already
    # know — applies, and re-explaining the job to them reads as amnesia.
    resumed = (
        "- This CONTINUES the conversation above — the owner asked for this here "
        "and is still in this thread. Pick it up mid-conversation: no greeting, "
        "no reintroducing the task, and honour whatever they already told you "
        "about what they want from it.\n"
        if payload.get("resumed") else
        "- The owner cannot see this thread's history, so give the result enough "
        "framing to stand on its own.\n"
    )
    if legion:
        who = f"legionnaire: {payload.get('worker') or 'unknown'}"
        opened = "a legionnaire you deployed earlier"
        again = "do NOT deploy another legionnaire"
        named = "One short line naming which legionnaire ran and what it was asked"
        empty = "(the worker returned nothing)"
    else:
        target = str(payload.get("to_agent") or "unknown").upper()
        who = f"agent: {target}"
        opened = f"{target}, which you dispatched a task to earlier,"
        again = "do NOT dispatch the task again"
        named = f"One short line naming {target} and what it was asked"
        empty = "(the agent returned nothing)"

    return (
        f"BACKGROUND WORK COMPLETE — {opened} has finished and the owner has not "
        "seen the result yet. No human is waiting in this turn; your job is to "
        "deliver the finding.\n\n"
        "- The work is ALREADY DONE. Do NOT re-run the research, do NOT repeat "
        f"the tool calls it made, and {again}. Everything you need is below.\n"
        f"- Write the owner's message FROM the findings. Lead with the answer, "
        f"not with preamble about who ran it. {named}, then the substance.\n"
        + resumed
        + (
            "- This run FAILED. Say so plainly, give the error, and say what "
            "you would try next. Do NOT invent a result to fill the gap.\n"
            if failed else
            "- Keep it to what the findings actually support. If they are "
            "thin or inconclusive, say that rather than padding.\n"
        )
        + f"- {delivery}\n\n"
        f"{who}\n"
        f"task: {payload.get('task') or '(unspecified)'}\n"
        f"status: {status}\n"
        f"ticket: {payload.get('ticket') or '(untracked)'}\n\n"
        "findings:\n"
        f"{payload.get('result') or empty}"
    )


def report_meta(payload: dict) -> dict | None:
    """The completion report, as structured data for the UI's collapsed card.

    A report seed is a wall of prompt — the framing, the instruction not to
    re-run the work, the raw findings. Rendered as a chat bubble it buries the
    answer under its own scaffolding, and the owner never asked for the
    scaffolding. So the seed's DISPLAY form is this: a folded row above the
    reply, in the same language as a tool call, that opens to show what was
    asked and what came back. Everything here is display-only; the model reads
    the seed text (`_clean` strips this block before history goes back).
    """
    kind = {"legion_report": "legion", "dispatch_report": "dispatch"}.get(payload.get("type"))
    if kind is None:
        return None
    return {
        "kind": kind,
        # Who did the work: a legionnaire's role, or the agent dispatched to.
        "from": str(payload.get("worker") or payload.get("to_agent") or "unknown"),
        "task": str(payload.get("task") or "")[:2000],
        "status": str(payload.get("status") or "ok"),
        "ticket": payload.get("ticket"),
        # The raw findings, as the worker returned them — the point of being
        # able to open the card at all is seeing what the reply was built from.
        "result": str(payload.get("result") or "")[:REPORT_DISPLAY_CHARS],
    }


def report_headline(report: dict) -> str:
    """The collapsed card's one line, and the fallback bubble text for a client
    that does not know about report cards."""
    who = report["from"].upper()
    verb = "reported back" if report["kind"] == "dispatch" else "finished"
    tail = "" if report["status"] == "ok" else f" — {report['status']}"
    return f"{who} {verb}{tail}"


def trigger_meta(payload: dict, output_mode: str) -> dict:
    """Display-only provenance for the seed turn, carried in the `_speda_meta`
    block the UI already knows how to recover on reload.

    A triggered run is a normal conversation in the app — but the owner did not
    write its opening message, and a bubble attributed to them would be a lie
    the next reader has no way to catch. This is what lets the UI label the
    sender as the automation instead. `_clean` strips the block before the
    history goes back to the model, so it costs nothing in the prompt.
    """
    meta = {
        # A completion report is not n8n's doing — the agent's own worker (a
        # legionnaire) or the agent it dispatched to woke it. The UI labels the
        # sender from this, and attributing it to the automation channel would
        # misreport who started the conversation.
        "source": {
            "legion_report": "legion",
            "dispatch_report": "agent",
        }.get(payload.get("type"), "n8n"),
        "from_agent": payload.get("to_agent") or "",
        "label": _label(payload),
        "job": payload.get("job") or payload.get("event") or payload.get("type") or "",
        "automation": payload.get("automation") or "",
        "output_mode": output_mode,
    }
    if (report := report_meta(payload)) is not None:
        meta["report"] = report
    return meta


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
    session_id: int | None = None,
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

    `session_id` continues an EXISTING conversation instead of opening a new
    one. n8n never passes it (each run is its own thread); a completion report
    does, so the answer lands in the chat where the owner asked for the work and
    the agent still has the conversation that explains WHY it was sent.
    """
    agent_id = profile.agent_id
    model = profile.allocate_model(triggered_by)

    session = await session_manager.get_or_create(
        db=db,
        user_id=user_id,
        triggered_by=triggered_by,
        model_used=model,
        agent_id=agent_id,
        session_id=session_id,
    )
    if session_id is not None and session.agent_id != agent_id:
        # Wrong room. Sessions are scoped by (user_id, agent_id), so appending
        # here would put this agent's turn in ANOTHER agent's transcript and
        # hand it that agent's history as context. Reachable when a dispatched
        # agent backgrounds a dispatch of its own: the room it inherited belongs
        # to whoever started the chain, not to it. Fall back to its own session.
        logger.warning(
            "report_room_mismatch",
            extra={
                "request_id": request_id, "agent_id": agent_id,
                "session_id": session_id, "session_agent": session.agent_id,
            },
        )
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
    meta = trigger_meta(payload, output_mode)
    seed_block: dict = {"type": "_speda_meta", "trigger": meta}
    if meta.get("report"):
        # What the BUBBLE would show, if anything ever falls back to showing one:
        # a headline, never the seed. The seed is scaffolding — the instructions
        # the agent needed to write the reply — and rendering it as a message
        # from the owner is both a lie and a wall of text over the answer.
        seed_block["text"] = report_headline(meta["report"])
    await session_manager.save_message(
        db,
        session.id,
        "user",
        [
            {"type": "text", "text": build_seed(payload, output_mode)},
            seed_block,
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
    # The same provenance the seed was persisted with, handed to the STREAM. A
    # client watching this turn arrive must render what a client reloading it
    # later renders — one card, from one dict, whichever way it got here.
    context.extra["trigger_meta"] = meta

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
            profile=profile,
            sanitize_model=bg_model,
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
    profile=None,
    sanitize_model: str = "",
) -> None:
    """Post-run delivery: stamp the automation, then push if asked.

    The text comes back out of the DB rather than off a stream, so what the
    owner is sent is the turn the transcript shows — including the runner's
    marker when a turn broke off early. Not all of it, though: delivery takes
    the turn's closing answer only, never the between-tool narration that
    precedes it (see chat_history.final_answer_text).
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
        if payload.get("voice"):
            delivered = await _deliver_voice(
                agent_id=agent_id, text=text, profile=profile,
                title=str(payload.get("automation") or ""), telegram_bots=telegram_bots,
                request_id=request_id, sanitize_model=sanitize_model,
            )
        else:
            delivered = await telegram_bots.deliver_message(agent_id, text)
        if not delivered:
            await _store_notification(db, agent_id, user_id, request_id, text, payload)
        logger.info(
            "trigger_push_delivered" if delivered else "trigger_push_stored",
            extra={"request_id": request_id, "chars": len(text), "status": status},
        )


async def _deliver_voice(
    *, agent_id: str, text: str, profile, title: str, telegram_bots, request_id: str,
    sanitize_model: str = "",
) -> bool:
    """Speak `text` and send it as a Telegram audio message instead of plain
    text — the point of an automation's "reply as voice" checkbox
    (composer.py bakes `voice: true` into the trigger payload; see
    automations/templates.py).

    Resolves voice + tuning through the SAME precedence /voice/speak uses
    (tts.resolve_voice/resolve_voice_settings): the owner's pin from
    Settings → Voices outranks the profile's own default. Falls back to
    plain text on ANY synthesis or delivery failure — a misconfigured or
    rate-limited TTS key must never mean the owner gets nothing instead of
    the briefing he actually asked to hear, only in a format he didn't ask
    for.

    `sanitize_model` is the caller's already-resolved background-tier model
    (Rule 10 — this module names none itself); passed to
    `tts.prepare_speech_text` so a stray unit or leftover "let me compose
    this" line gets a real model's read on it, not just the fixed regex list.
    Text is prepared ONCE and the same spoken string is used for both the
    audio and its caption/fallback — the owner must never see a transcript
    that says something different from what the clip actually says.
    """
    from app.services import tts

    voice_ref = tts.resolve_voice(None, agent_id, profile=profile)
    voice_settings = tts.resolve_voice_settings(agent_id)
    try:
        spoken = await tts.prepare_speech_text(text, sanitize_model=sanitize_model)
        audio = await tts.synthesize_prepared(spoken, voice_ref, voice_settings=voice_settings)
    except tts.TTSError as exc:
        logger.warning(
            "automation_voice_synthesis_failed",
            extra={"request_id": request_id, "agent_id": agent_id, "error": str(exc)},
        )
        return await telegram_bots.deliver_message(agent_id, text)

    # Truncate the NAME first, then append the extension — slicing the whole
    # "name.mp3" string can land mid-extension for a long title and hand
    # Telegram a file with no recognizable type.
    short_title = (title or agent_id)[:60]
    ok = await telegram_bots.deliver_voice(
        agent_id, audio, f"{short_title}.mp3", title=short_title, caption=spoken,
    )
    if not ok:
        return await telegram_bots.deliver_message(agent_id, spoken)
    # Telegram caps a caption at 1024 chars — send_audio truncates silently to
    # respect that. A voice briefing stays well under it (the polisher's own
    # 80-120 word budget), but the transcript itself must never be the thing
    # that gets cut, so anything that could overflow the cap also goes out as
    # its own message, in full.
    if len(spoken) > 1000:
        await telegram_bots.deliver_message(agent_id, spoken)
    return True


async def _last_assistant_text(db: AsyncSession, session_id: int) -> str:
    """What gets delivered for the newest assistant turn: its closing answer,
    with the model's between-tool narration left behind in the transcript.

    A briefing is written after the last tool returns; everything before that is
    the agent talking to itself about which tool to reach for next. On a push
    there is no stream and no tool disclosure to give that text a context, so it
    lands in Telegram as a preamble of stage directions above the report."""
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
    if isinstance(row.content, str):
        return row.content.strip()
    return final_answer_text(row.content)


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


# ── Background completion reports ────────────────────────────────────────────


# How long a finished report waits for the room to be free before giving up and
# opening its own session. Generous on purpose: the alternative to waiting is
# the context loss this whole path exists to avoid, and the waiting happens in
# a detached background task with a durable ticket behind it.
_ROOM_WAIT_S = 300.0


async def _await_free_room(turns, session_id: int | None) -> int | None:
    """The room to report into, once nothing else is talking in it.

    Two turns writing one session interleave — TurnRegistry has no per-session
    lock — so a report that lands while the owner is mid-conversation waits for
    that turn to settle instead of cutting into it. Returns None when there is
    no room to go back to, or when the wait ran out: the caller then opens a
    fresh session, which costs the thread's context but never the report.
    """
    if session_id is None:
        return None
    deadline = time.monotonic() + _ROOM_WAIT_S
    while True:
        live = turns.active(session_id=session_id)
        if not live:
            return session_id
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            logger.warning(
                "report_room_busy",
                extra={"session_id": session_id, "waited_s": int(_ROOM_WAIT_S)},
            )
            return None
        try:
            await turns.wait(live[0]["request_id"], timeout=remaining)
        except TimeoutError:
            logger.warning(
                "report_room_busy",
                extra={"session_id": session_id, "waited_s": int(_ROOM_WAIT_S)},
            )
            return None


async def _start_report_turn(
    *,
    kind: str,
    profile,
    payload: dict,
    orchestrator,
    turns,
    session_manager,
    telegram_bots,
    agent_proxy,
    ws_manager,
    user_id: int,
    room_session_id: int | None = None,
) -> None:
    """Wake `profile`'s agent with a finished background result, as a push turn.

    Shared by both reporters below — a legionnaire finishing and a dispatched
    agent finishing are the same event from the waiting agent's side, and the
    delivery chain (push → Telegram → notification row) is the same one.

    The turn continues the conversation the work was ordered from
    (`room_session_id`) rather than starting a clean one. That thread is where
    the owner's constraints, follow-ups and half of the reason the job was sent
    live; reporting into a blank session threw all of it away and made the agent
    read its own finding cold.
    """
    request_id = str(uuid.uuid4())
    room = await _await_free_room(turns, room_session_id)
    # Its own DB session: the turn that launched this work finished long ago and
    # its session is gone.
    async with AsyncSessionLocal() as db:
        started, session_id = await start_trigger_turn(
            db=db,
            profile=profile,
            # Whether the thread is there is something the seed has to know: an
            # agent picking up its own conversation must not reintroduce the job
            # the owner is already looking at.
            payload={**payload, "resumed": room is not None},
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
            session_id=room,
        )
    logger.info(
        f"{kind}_report_started" if started else f"{kind}_report_refused",
        extra={
            "request_id": request_id,
            "agent_id": profile.agent_id,
            "status": payload.get("status"),
            "ticket": payload.get("ticket"),
            "session_id": session_id,
            "resumed_room": room is not None,
        },
    )


def make_dispatch_reporter(
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
    """Build the callback a finished BACKGROUND dispatch fires to report in.

    The Legion's counterpart below has done this since background workers
    existed; a background dispatch (dispatch_agent with background=true, and
    every arm of a backgrounded House Party broadcast) ended in silence instead —
    the answer landed in its agent_messages ticket and stayed there until the
    owner thought to ask "is that done yet?". That is the one outcome that makes
    sending a job away pointless: the owner has to poll the assistant they
    delegated to. Completion now starts a real push turn on the agent that
    dispatched, so it reads the answer and delivers it unprompted.

    Blocking dispatches are NOT reported — their result returns into the caller's
    turn, where the agent is already holding it and already replying. Reporting
    those would double-send.
    """

    async def report(
        *,
        agent_id: str,
        to_agent: str,
        task: str,
        result: str,
        status: str,
        ticket: int | None = None,
        room_session_id: int | None = None,
    ) -> None:
        profile = profiles.get(agent_id)
        if profile is None:
            logger.warning("dispatch_report_unknown_agent", extra={"agent_id": agent_id})
            return

        await _start_report_turn(
            kind="dispatch",
            room_session_id=room_session_id,
            profile=profile,
            payload={
                "type": "dispatch_report",
                "job": f"Report from {to_agent.upper()}",
                "to_agent": to_agent,
                "task": task,
                "result": result,
                "status": status,
                "ticket": ticket,
            },
            orchestrator=orchestrator,
            turns=turns,
            session_manager=session_manager,
            telegram_bots=telegram_bots,
            agent_proxy=agent_proxy,
            ws_manager=ws_manager,
            user_id=user_id,
        )

    return report


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
        room_session_id: int | None = None,
    ) -> None:
        profile = profiles.get(agent_id)
        if profile is None:
            logger.warning(
                "legion_report_unknown_agent", extra={"agent_id": agent_id}
            )
            return

        await _start_report_turn(
            kind="legion",
            room_session_id=room_session_id,
            profile=profile,
            payload={
                "type": "legion_report",
                "job": f"{worker_id} report",
                "worker": worker_id,
                "task": task,
                "result": result,
                "status": status,
                "ticket": ticket,
            },
            orchestrator=orchestrator,
            turns=turns,
            session_manager=session_manager,
            telegram_bots=telegram_bots,
            agent_proxy=agent_proxy,
            ws_manager=ws_manager,
            user_id=user_id,
        )

    return report
