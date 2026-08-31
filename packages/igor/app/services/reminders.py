# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Persistent reminders — "keep asking until I answer", without spending a turn.

The cheap-probe rule applies here more than anywhere: a reminder that re-asks
every 5 minutes would, if each ask were an agentic turn, cost ~12 turns an hour
to say the same sentence. So the question text and its answer options come from
the n8n config node, Igor sends them verbatim over the agent's own Telegram bot,
and no model is involved in asking, re-asking, or recording the answer.

Two halves, mirroring the mail and web watches:

  the tick   POST /reminders/tick — n8n hands over its reminder definitions;
             Igor decides which are due, sends them, counts the asks, and gives
             up after max_asks. Zero tokens.
  the answer A tap on an inline button → callback → resolved here, deterministic.
             A free-text "aldım" in the chat is handled by the agent's own turn
             (which was happening anyway because the owner spoke) through the
             `reminders` skill.

Definitions live in n8n and state lives here. That is what makes the workflow a
forkable template: duplicate it, point it at another agent, rewrite the list,
and this module needs no change at all.
"""

import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import owner_now
from app.models.reminder import ReminderCycle
from app.models.reminder_definition import ReminderDefinition

logger = logging.getLogger(__name__)

# Defaults, overridable per reminder from the n8n config node.
DEFAULT_EVERY_MINUTES = 5
DEFAULT_MAX_ASKS = 10

# callback_data is capped at 64 bytes by Telegram, so the prefix stays tiny.
CB_PREFIX = "rm"


def callback_value(cycle_id: int, value: str) -> str:
    """The token that travels on a button and comes back on the tap."""
    return f"{CB_PREFIX}:{cycle_id}:{value}"[:64]


def parse_callback(data: str) -> tuple[int, str] | None:
    """(cycle_id, answer) from a tapped button, or None if it isn't ours."""
    parts = (data or "").split(":", 2)
    if len(parts) != 3 or parts[0] != CB_PREFIX:
        return None
    try:
        return int(parts[1]), parts[2]
    except ValueError:
        return None


def _due_today(spec: dict, now: datetime) -> bool:
    """Has this reminder's clock time passed today, on a day it runs?

    `at` is "HH:MM" wall-clock; `days` is "*" or a comma list of weekday numbers
    (1=Monday … 7=Sunday), matching cron's convention so the n8n schedule and
    this agree on what "weekdays" means.
    """
    at = str(spec.get("at") or "").strip()
    if not at:
        return True  # no clock → due whenever the tick runs (e.g. an ad-hoc reminder)
    days = str(spec.get("days") or "*").strip()
    if days != "*":
        allowed = {d.strip() for d in days.split(",") if d.strip()}
        if str(now.isoweekday()) not in allowed:
            return False
    try:
        hh, mm = (int(x) for x in at.split(":", 1))
    except ValueError:
        logger.warning("reminder_bad_time", extra={"at": at})
        return False
    return (now.hour, now.minute) >= (hh, mm)


async def _open_cycle(db: AsyncSession, reminder_id: str, day: str) -> ReminderCycle | None:
    row = await db.execute(
        select(ReminderCycle).where(
            ReminderCycle.reminder_id == reminder_id,
            ReminderCycle.status == "open",
        ).order_by(ReminderCycle.id.desc()).limit(1)
    )
    return row.scalars().first()


async def _closed_today(db: AsyncSession, reminder_id: str, day: str) -> bool:
    row = await db.execute(
        select(ReminderCycle.id).where(
            ReminderCycle.reminder_id == reminder_id,
            ReminderCycle.day == day,
            ReminderCycle.status != "open",
        ).limit(1)
    )
    return row.scalars().first() is not None


def _normalise_options(options) -> list[dict]:
    """[{label, value}] from whatever the caller supplied — the n8n list, or an
    agent's tool call, which may pass bare strings."""
    out = []
    for opt in (options or [])[:6]:  # more than six buttons is a menu, not a question
        if isinstance(opt, str):
            label, value = opt, opt.lower().replace(" ", "_")[:24]
        else:
            label = str(opt.get("label") or opt.get("value") or "OK")
            value = str(opt.get("value") or label).lower().replace(" ", "_")[:24]
        out.append({"label": label[:40], "value": value})
    return out or [{"label": "✅ Done", "value": "done"}]


def _buttons(spec: dict, cycle_id: int) -> list[tuple[str, str]]:
    """Answer buttons for one reminder. A reminder with no options configured
    still gets a single confirm button — the whole feature is "until you
    answer", so there must always be something to tap."""
    return [(o["label"], callback_value(cycle_id, o["value"]))
            for o in _normalise_options(spec.get("options"))]


async def list_definitions(db: AsyncSession, agent_id: str = "", enabled_only: bool = False) -> list[dict]:
    """The owner's standing reminders, as the clients show them."""
    q = select(ReminderDefinition)
    if agent_id:
        q = q.where(ReminderDefinition.agent_id == agent_id)
    if enabled_only:
        q = q.where(ReminderDefinition.enabled.is_(True))
    rows = (await db.execute(q.order_by(ReminderDefinition.at, ReminderDefinition.id))).scalars().all()
    return [_definition_dict(r) for r in rows]


def _definition_dict(r: ReminderDefinition) -> dict:
    try:
        options = json.loads(r.options_json) if r.options_json else []
    except ValueError:
        options = []
    return {
        "id": r.id, "agent": r.agent_id, "text": r.text, "at": r.at, "days": r.days,
        "options": _normalise_options(options),
        "every_minutes": r.every_minutes, "max_asks": r.max_asks,
        "enabled": bool(r.enabled),
        "updated_at": r.updated_at.isoformat(timespec="seconds") if r.updated_at else "",
    }


async def upsert_definition(db: AsyncSession, spec: dict) -> dict:
    """Create or replace one definition. The id is the owner's slug."""
    rid = str(spec.get("id") or "").strip()[:64]
    if not rid or not str(spec.get("text") or "").strip():
        return {"status": "invalid", "detail": "id and text are required"}

    row = (await db.execute(
        select(ReminderDefinition).where(ReminderDefinition.id == rid)
    )).scalars().first()
    if row is None:
        row = ReminderDefinition(id=rid)
        db.add(row)

    row.agent_id = (spec.get("agent") or "speda").strip()[:32]
    row.text = str(spec["text"])
    row.at = str(spec.get("at") or "").strip()[:5]
    row.days = str(spec.get("days") or "*").strip()[:32]
    row.options_json = json.dumps(_normalise_options(spec.get("options")), ensure_ascii=False)
    row.every_minutes = max(1, min(int(spec.get("every_minutes") or DEFAULT_EVERY_MINUTES), 1440))
    row.max_asks = max(1, min(int(spec.get("max_asks") or DEFAULT_MAX_ASKS), 200))
    row.enabled = bool(spec.get("enabled", True))
    row.updated_at = datetime.utcnow()
    await db.commit()
    logger.info("reminder_definition_saved", extra={"reminder_id": rid, "agent_id": row.agent_id})
    return {"status": "ok", "definition": _definition_dict(row)}


async def delete_definition(db: AsyncSession, reminder_id: str) -> dict:
    """Remove a definition. History for that id is deliberately left alone —
    deleting a reminder should not erase the record of having taken it."""
    row = (await db.execute(
        select(ReminderDefinition).where(ReminderDefinition.id == reminder_id)
    )).scalars().first()
    if row is None:
        return {"status": "noop", "reminder_id": reminder_id}
    await db.delete(row)
    await db.commit()
    logger.info("reminder_definition_deleted", extra={"reminder_id": reminder_id})
    return {"status": "ok", "reminder_id": reminder_id}


async def _merged_specs(db: AsyncSession, agent_id: str, payload: list[dict]) -> list[dict]:
    """This tick's reminders: the owner's stored definitions plus anything the
    workflow sent inline. Stored wins on an id collision — it is the surface the
    owner edits, so it is the one they expect to see take effect."""
    stored = await list_definitions(db, agent_id=agent_id, enabled_only=True)
    by_id = {s["id"]: s for s in stored}
    for spec in payload or []:
        rid = str(spec.get("id") or "").strip()
        if rid and rid in by_id:
            logger.info("reminder_definition_shadowed", extra={"reminder_id": rid})
            continue
        by_id[rid or f"_inline_{len(by_id)}"] = spec
    return list(by_id.values())


async def tick(db: AsyncSession, agent_id: str, reminders: list[dict], bots, now: datetime | None = None) -> dict:
    """One poll. Opens due reminders, re-asks open ones, gives up on exhausted
    ones. Returns a per-reminder summary for the n8n execution log.

    Never raises: this runs every few minutes forever, and a malformed entry in
    the config node must not stop the other reminders from firing.
    """
    now = now or owner_now()
    day = now.strftime("%Y-%m-%d")
    bot = bots.get(agent_id)
    sent, gave_up, waiting, skipped = [], [], [], []

    # The owner's stored definitions are the primary source; the workflow's
    # inline list still works so an existing fork keeps firing.
    specs = await _merged_specs(db, agent_id, reminders)
    for spec in specs:
        try:
            reminder_id = str(spec.get("id") or "").strip()
            if not reminder_id or not str(spec.get("text") or "").strip():
                skipped.append(spec.get("id") or "(no id)")
                continue

            every = int(spec.get("every_minutes") or DEFAULT_EVERY_MINUTES)
            max_asks = int(spec.get("max_asks") or DEFAULT_MAX_ASKS)
            cycle = await _open_cycle(db, reminder_id, day)

            # ── No open cycle: should one start? ────────────────────────────
            if cycle is None:
                if not _due_today(spec, now) or await _closed_today(db, reminder_id, day):
                    waiting.append(reminder_id)
                    continue
                cycle = ReminderCycle(
                    reminder_id=reminder_id,
                    agent_id=agent_id,
                    question=str(spec["text"]),
                    status="open",
                    max_asks=max_asks,
                    day=day,
                    opened_at=now,
                )
                db.add(cycle)
                await db.commit()
                await db.refresh(cycle)

            # ── Open cycle: is it time to ask again? ────────────────────────
            elif cycle.next_ask_at and cycle.next_ask_at > now:
                waiting.append(reminder_id)
                continue

            # ── Out of patience ─────────────────────────────────────────────
            if cycle.asks >= max_asks:
                cycle.status = "gave_up"
                cycle.closed_at = now
                await db.commit()
                if bot and cycle.chat_id and cycle.last_message_id:
                    await bot.clear_buttons(cycle.chat_id, cycle.last_message_id)
                gave_up.append(reminder_id)
                logger.info(
                    "reminder_gave_up",
                    extra={"reminder_id": reminder_id, "asks": cycle.asks},
                )
                continue

            # ── Ask ─────────────────────────────────────────────────────────
            if bot is None or not bot.configured:
                logger.warning("reminder_no_bot", extra={"agent_id": agent_id})
                skipped.append(reminder_id)
                continue

            # Remember what it takes to re-ask this cycle without the spec.
            cycle.options_json = json.dumps(_normalise_options(spec.get("options")),
                                            ensure_ascii=False)
            cycle.every_minutes = every

            attempt = cycle.asks + 1
            text = str(spec["text"])
            if attempt > 1:
                # Say the quiet part: the owner should be able to tell a repeat
                # from a fresh reminder at a glance, and know when it stops.
                text = f"{text}\n\n<i>(reminder {attempt}/{max_asks})</i>"

            # Clear the previous ask's buttons so only the newest is tappable.
            if cycle.chat_id and cycle.last_message_id:
                await bot.clear_buttons(cycle.chat_id, cycle.last_message_id)

            message_id = await bot.send_question(text, _buttons(spec, cycle.id))
            cycle.asks = attempt
            cycle.last_ask_at = now
            cycle.next_ask_at = now + timedelta(minutes=every)
            if message_id:
                cycle.last_message_id = message_id
                from app.core.runtime_state import get_telegram_owner_id

                cycle.chat_id = cycle.chat_id or get_telegram_owner_id()
            await db.commit()
            sent.append({"reminder_id": reminder_id, "ask": attempt, "of": max_asks})
            logger.info(
                "reminder_asked",
                extra={"reminder_id": reminder_id, "agent_id": agent_id, "ask": attempt},
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "reminder_tick_failed",
                extra={"reminder_id": spec.get("id"), "error": str(exc)},
            )
            skipped.append(spec.get("id") or "(error)")

    # ── Cycles this tick's config knows nothing about ────────────────────────
    # An agent-opened reminder (personalised text composed in a turn) has no
    # entry in any n8n list, so the loop above never sees it. Without this it
    # would be asked exactly once and then sit open forever — which is the one
    # behaviour the whole feature exists to prevent.
    known = {str(s.get("id") or "") for s in specs}
    for cycle in await _open_cycles_for(db, agent_id):
        if cycle.reminder_id in known:
            continue
        if cycle.next_ask_at and cycle.next_ask_at > now:
            waiting.append(cycle.reminder_id)
            continue
        result = await _ask_open_cycle(db, cycle, bot, now)
        (gave_up if result == "gave_up" else sent if result == "asked" else skipped).append(
            cycle.reminder_id if result != "asked"
            else {"reminder_id": cycle.reminder_id, "ask": cycle.asks, "of": cycle.max_asks}
        )

    return {
        "status": "ok",
        "agent": agent_id,
        "sent": sent,
        "gave_up": gave_up,
        "waiting": waiting,
        "skipped": skipped,
    }


async def _open_cycles_for(db: AsyncSession, agent_id: str) -> list[ReminderCycle]:
    rows = await db.execute(
        select(ReminderCycle).where(
            ReminderCycle.status == "open", ReminderCycle.agent_id == agent_id
        )
    )
    return list(rows.scalars().all())


async def _ask_open_cycle(db: AsyncSession, cycle: ReminderCycle, bot, now: datetime) -> str:
    """Re-ask (or give up on) a cycle using only what the cycle itself stores.
    Returns 'asked' | 'gave_up' | 'skipped'."""
    if cycle.asks >= cycle.max_asks:
        cycle.status = "gave_up"
        cycle.closed_at = now
        await db.commit()
        if bot and cycle.chat_id and cycle.last_message_id:
            await bot.clear_buttons(cycle.chat_id, cycle.last_message_id)
        logger.info("reminder_gave_up",
                    extra={"reminder_id": cycle.reminder_id, "asks": cycle.asks})
        return "gave_up"

    if bot is None or not bot.configured:
        return "skipped"

    try:
        options = json.loads(cycle.options_json) if cycle.options_json else []
    except ValueError:
        options = []
    buttons = [(o["label"], callback_value(cycle.id, o["value"]))
               for o in _normalise_options(options)]

    attempt = cycle.asks + 1
    text = cycle.question
    if attempt > 1:
        text = f"{text}\n\n<i>(reminder {attempt}/{cycle.max_asks})</i>"
    if cycle.chat_id and cycle.last_message_id:
        await bot.clear_buttons(cycle.chat_id, cycle.last_message_id)

    message_id = await bot.send_question(text, buttons)
    cycle.asks = attempt
    cycle.last_ask_at = now
    cycle.next_ask_at = now + timedelta(minutes=cycle.every_minutes or DEFAULT_EVERY_MINUTES)
    if message_id:
        from app.core.runtime_state import get_telegram_owner_id

        cycle.last_message_id = message_id
        cycle.chat_id = cycle.chat_id or get_telegram_owner_id()
    await db.commit()
    logger.info("reminder_asked",
                extra={"reminder_id": cycle.reminder_id, "agent_id": cycle.agent_id,
                       "ask": attempt, "source": "agent"})
    return "asked"


async def open_ask(
    db: AsyncSession,
    *,
    agent_id: str,
    reminder_id: str,
    text: str,
    options=None,
    every_minutes: int = DEFAULT_EVERY_MINUTES,
    max_asks: int = DEFAULT_MAX_ASKS,
    bots=None,
) -> dict:
    """Open a reminder whose text an AGENT just composed, and send it now.

    This is the bridge between the two halves of the system. The expensive,
    personalised part — "you trained today, rest tomorrow, pack your bag" —
    happens once, in a turn that was going to run anyway. Everything after it
    is free: the tick re-asks this exact text until it is answered, and a tap
    closes it without a model.

    Idempotent per day: asking again while a cycle for the same reminder_id is
    already open returns that cycle instead of opening a second one, so a
    retried trigger cannot produce two competing questions.
    """
    now = owner_now()
    day = now.strftime("%Y-%m-%d")
    reminder_id = (reminder_id or "ask").strip()[:64]

    existing = await _open_cycle(db, reminder_id, day)
    if existing is not None:
        return {"status": "already_open", "reminder_id": reminder_id,
                "cycle_id": existing.id, "asks": existing.asks}

    cycle = ReminderCycle(
        reminder_id=reminder_id,
        agent_id=agent_id,
        question=str(text),
        status="open",
        max_asks=max(1, min(int(max_asks), 200)),
        every_minutes=max(1, min(int(every_minutes), 1440)),
        options_json=json.dumps(_normalise_options(options), ensure_ascii=False),
        day=day,
        opened_at=now,
    )
    db.add(cycle)
    await db.commit()
    await db.refresh(cycle)

    bot = bots.get(agent_id) if bots else None
    result = await _ask_open_cycle(db, cycle, bot, now)
    if result != "asked":
        return {"status": "opened_not_sent", "reminder_id": reminder_id,
                "cycle_id": cycle.id, "detail": "no usable Telegram bot for this agent"}
    return {"status": "ok", "reminder_id": reminder_id, "cycle_id": cycle.id,
            "max_asks": cycle.max_asks, "every_minutes": cycle.every_minutes}


async def answer(
    db: AsyncSession, cycle_id: int, value: str, via: str = "button", bots=None
) -> dict:
    """Record an answer and close the cycle. Idempotent — a double tap on a
    stale message reports the existing answer instead of overwriting it."""
    cycle = (await db.execute(
        select(ReminderCycle).where(ReminderCycle.id == cycle_id)
    )).scalars().first()
    if cycle is None:
        return {"status": "unknown", "detail": "no such reminder"}
    if cycle.status != "open":
        return {"status": "already", "answer": cycle.answer, "reminder_id": cycle.reminder_id}

    cycle.status = "answered"
    cycle.answer = value[:64]
    cycle.answered_via = via
    cycle.closed_at = owner_now()
    await db.commit()

    if bots and cycle.chat_id and cycle.last_message_id:
        bot = bots.get(cycle.agent_id)
        if bot:
            await bot.clear_buttons(cycle.chat_id, cycle.last_message_id)

    logger.info(
        "reminder_answered",
        extra={"reminder_id": cycle.reminder_id, "answer": value, "via": via,
               "asks": cycle.asks},
    )
    return {"status": "ok", "reminder_id": cycle.reminder_id, "answer": cycle.answer,
            "asks": cycle.asks}


async def answer_latest(
    db: AsyncSession, value: str, reminder_id: str = "", agent_id: str = "", bots=None
) -> dict:
    """Close the most recent OPEN cycle — the path a free-text 'I took it' takes.

    Without a reminder_id it resolves the newest open question, which is almost
    always the one the owner is replying to: they are answering the thing that
    just buzzed. An agent that knows better passes reminder_id explicitly.
    """
    q = select(ReminderCycle).where(ReminderCycle.status == "open")
    if reminder_id:
        q = q.where(ReminderCycle.reminder_id == reminder_id)
    if agent_id:
        q = q.where(ReminderCycle.agent_id == agent_id)
    cycle = (await db.execute(q.order_by(ReminderCycle.id.desc()).limit(1))).scalars().first()
    if cycle is None:
        return {"status": "none_open", "detail": "nothing is waiting for an answer"}
    return await answer(db, cycle.id, value, via="chat", bots=bots)


async def list_open(db: AsyncSession, agent_id: str = "") -> list[dict]:
    """Questions currently waiting on the owner. Used by the agent tool so a
    turn can see what is outstanding before guessing what 'yes' refers to."""
    q = select(ReminderCycle).where(ReminderCycle.status == "open")
    if agent_id:
        q = q.where(ReminderCycle.agent_id == agent_id)
    rows = (await db.execute(q.order_by(ReminderCycle.id.desc()).limit(20))).scalars().all()
    return [{
        "cycle_id": c.id,
        "reminder_id": c.reminder_id,
        "agent_id": c.agent_id,
        "question": c.question,
        "asks": c.asks,
        "max_asks": c.max_asks,
        "opened_at": c.opened_at.isoformat(timespec="seconds") if c.opened_at else "",
    } for c in rows]


async def history(db: AsyncSession, reminder_id: str = "", limit: int = 30) -> list[dict]:
    """Closed cycles, newest first — this is what makes 'did I take it on
    Tuesday?' answerable."""
    q = select(ReminderCycle).where(ReminderCycle.status != "open")
    if reminder_id:
        q = q.where(ReminderCycle.reminder_id == reminder_id)
    rows = (await db.execute(
        q.order_by(ReminderCycle.id.desc()).limit(max(1, min(limit, 200)))
    )).scalars().all()
    return [{
        "reminder_id": c.reminder_id,
        "day": c.day,
        "status": c.status,
        "answer": c.answer,
        "via": c.answered_via,
        "asks": c.asks,
        "closed_at": c.closed_at.isoformat(timespec="seconds") if c.closed_at else "",
    } for c in rows]
