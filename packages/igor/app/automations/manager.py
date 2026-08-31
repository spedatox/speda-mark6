# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Automation manager — the one place automations are created, listed, toggled and
deleted. Both Speda's tool (skills/automations.py) and the Settings UI router
(routers/automations.py) call through here, so the two views can never drift.

Flow for create: validate + compose the spec into n8n workflow JSON → POST it
to n8n → activate it → persist the local Automation row mapping name/intent/
spec to the n8n workflow id. n8n stays the sole scheduler; the local row is
metadata for display and delivery context only.
"""

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.automations import composer
from app.automations import schedule as sched
from app.automations import templates
from app.config import settings
from app.models.automation import Automation
from app.services.n8n_api import N8nClient

logger = logging.getLogger(__name__)

# The queue kind that upgrades raw instructions. Named here because the manager
# enqueues it and services/automation_intent.py handles it, and a job kind
# spelled differently in those two places is a job that never runs.
POLISH_JOB = "automation_intent_polish"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _webhook_url(spec: dict) -> str | None:
    """The callable URL of a webhook watcher. n8n serves an ACTIVE workflow's
    webhook at {n8n}/webhook/{path}. n8n_api_url is the internal compose address,
    so this is the in-network URL — reachable publicly only if the deployment
    routes n8n's /webhook through its public domain."""
    path = spec.get("webhook_path")
    if not path:
        return None
    return f"{settings.n8n_api_url.rstrip('/')}/webhook/{path}"


def _as_dict(a: Automation) -> dict:
    spec = json.loads(a.spec or "{}")
    d = {
        "id": a.id,
        "agent_id": a.agent_id,
        "n8n_workflow_id": a.n8n_workflow_id,
        "name": a.name,
        "kind": a.kind,
        "intent": a.intent,
        "spec": spec,
        "active": a.active,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "expires_at": a.expires_at.isoformat() if a.expires_at else None,
        "last_fired_at": a.last_fired_at.isoformat() if a.last_fired_at else None,
        "summary": composer.describe(spec),
        # The owner-facing half. `schedule` is structure, not a sentence, so
        # Heartbreaker can render "Günde bir · 09:00" or "Daily · 09:00" from
        # the same payload; `instruction` is the editable content half, kept
        # separate from the assembled `intent` n8n actually fires with.
        "template": spec.get("template"),
        "schedule": composer.display(spec),
        "instruction": spec.get("instruction"),
        "instruction_raw": spec.get("instruction_raw"),
        "intent_status": spec.get("intent_status"),
        "options": spec.get("options"),
        "every_minutes": spec.get("every_minutes"),
        "max_asks": spec.get("max_asks"),
        "day_flags": spec.get("day_flags"),
        # The Hook half — structured watcher config, same "never a sentence"
        # rule as `schedule` above. None for anything that isn't a Hook.
        "hook": composer.hook_display(spec),
        "url": spec.get("url"),
        "look_for": spec.get("look_for"),
        "domain": spec.get("domain"),
        "recipient": spec.get("recipient"),
        "interval_minutes": spec.get("interval_minutes"),
        # Push automations only (composer.compose() ignores it otherwise) —
        # whether the reply is spoken through the firing agent's TTS voice
        # and sent as a Telegram audio message instead of plain text.
        "voice": bool(spec.get("voice")),
    }
    if a.kind == "webhook":
        d["webhook_url"] = _webhook_url(spec)
    return d


def _prepare(spec: dict) -> dict:
    """Validate and canonicalise a TEMPLATED spec, in place-ish, before anything
    reaches n8n. Untemplated specs (the agent tool's raw watcher kinds) pass
    through untouched — they have no schedule/hook block and no transport
    mechanics to bolt on.

    Branches on which of the two template FAMILIES the spec belongs to
    (templates.py): a schedule template gets the structured frequency/at/days
    machinery; a Hook fires on an event instead and gets a plain polling
    interval with no clock at all — forcing it through schedule.normalize()
    would demand an 'at' time that means nothing for "wake me when this page
    changes".

    Raises ValueError with an owner-readable message naming the field and fix.
    """
    template = spec.get("template")
    if not template:
        return spec

    if template in templates.SCHEDULE_TEMPLATES:
        spec["kind"] = "schedule"
        spec["schedule"] = sched.normalize(spec.get("schedule") or {})
    elif template in templates.HOOK_TEMPLATES:
        spec["kind"] = "mail_watch" if template == "hook_mail" else "web_watch"
        if template == "hook_address":
            # An address watch fires on ANY change — a stray look_for left over
            # from switching templates would silently turn it into a keyword
            # watch instead, which is exactly the distinction the owner picked
            # between when he chose this template.
            spec.pop("look_for", None)
        spec.setdefault("interval_minutes", 15 if template == "hook_mail" else 360)
    templates.validate(spec)

    # The owner's own words are kept for good, separately from `instruction`:
    # the polisher rewrites the latter, and without the original there is
    # nothing to show him in the editor or to re-polish from if it goes wrong.
    if not spec.get("instruction_raw"):
        spec["instruction_raw"] = spec.get("instruction", "")
    spec.setdefault("intent_status", "raw")

    # A one-off's expiry is what makes it a one-off — cron alone would bring it
    # back next year (app/automations/schedule.py). Hooks have no schedule and
    # so never imply one here — duration_days (create_automation) is still how
    # a Hook gets time-boxed.
    if template in templates.SCHEDULE_TEMPLATES:
        implied = sched.expiry_for(spec["schedule"])
        if implied:
            spec["expires_at"] = implied
    return spec


async def push_to_n8n(spec: dict, agent_id: str, workflow_id: str | None) -> str:
    """Compose the spec and create or update the workflow in n8n. Returns the
    workflow id. Raises ValueError carrying n8n's own validation message, which
    is the only thing that ever says what was actually wrong with the JSON."""
    workflow = composer.compose(spec, agent_id)  # raises ValueError on a bad spec

    n8n = N8nClient()
    if not n8n.configured:
        raise ValueError(
            "n8n is not configured (N8N_API_KEY missing). Open n8n → Settings → "
            "n8n API → create a key, then set N8N_API_KEY in the backend .env."
        )
    if workflow_id:
        updated = await n8n.update_workflow(workflow_id, workflow)
        if updated is None:
            raise ValueError(
                "n8n rejected the updated workflow or is unreachable"
                + (f": {n8n.last_error}" if n8n.last_error else ".")
            )
        return workflow_id

    created = await n8n.create_workflow(workflow)
    if not created or not created.get("id"):
        detail = n8n.last_error
        raise ValueError(
            "n8n rejected the composed workflow or is unreachable"
            + (f": {detail}" if detail else " — check the backend logs (n8n_request_failed).")
        )
    return str(created["id"])


async def create_automation(
    spec: dict, db: AsyncSession, agent_id: str = "speda", model: str = ""
) -> dict:
    """Compose → push to n8n → activate → persist. Returns the automation dict,
    or raises ValueError with a actionable message (bad spec / n8n unreachable)
    that Speda can read and repair. agent_id is the creating agent — the watcher
    fires back through that agent's /trigger and is voiced by it.

    `model` is the background-tier model the intent polisher should run on,
    resolved by the CALLER from the owning agent's profile — routers have
    app.state.profiles, this layer does not, and Rule 10 keeps model IDs out of
    it either way. Empty means "do not polish": the owner's own wording is what
    runs, which is exactly right for an agent-authored spec, since the
    manage_automations tool already requires an executable intent."""
    # Idempotency guard — the daily-brief bug spawned FIVE identical "morning
    # briefing" workflows because nothing stopped a re-create. Refuse to stack a
    # second active automation with the same name; tell the agent to reuse or
    # delete+replace the existing one instead. Actionable ValueError so the
    # agent can self-correct in the same turn.
    name = (spec.get("name") or "").strip()
    if name:
        existing = (
            await db.execute(
                select(Automation).where(
                    Automation.name == name, Automation.active.is_(True)
                )
            )
        ).scalars().first()
        if existing is not None:
            raise ValueError(
                f"An active automation named '{name}' already exists "
                f"(id {existing.id}). Don't create a duplicate — either reuse it, "
                f"or delete it (action='delete', automation_id={existing.id}) and "
                f"create the replacement. Call action='list' to review first."
            )

    # "track this for a month" → concrete expiry the gate node enforces.
    duration_days = spec.pop("duration_days", None)
    if duration_days and not spec.get("expires_at"):
        spec["expires_at"] = (_now() + timedelta(days=float(duration_days))).isoformat()

    spec = _prepare(spec)
    workflow_id = await push_to_n8n(spec, agent_id, None)

    activated = await N8nClient().set_active(workflow_id, True)
    if not activated:
        logger.warning("automation_created_inactive", extra={"workflow_id": workflow_id})

    row = Automation(
        user_id=1,
        agent_id=agent_id,
        n8n_workflow_id=workflow_id,
        name=spec.get("name") or "Speda automation",
        kind=spec["kind"],
        intent=composer_intent(spec),
        spec=json.dumps(spec),
        active=bool(activated),
        expires_at=(
            datetime.fromisoformat(spec["expires_at"]) if spec.get("expires_at") else None
        ),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    logger.info(
        "automation_created",
        extra={"automation_id": row.id, "workflow_id": workflow_id, "kind": row.kind},
    )
    # The owner's phrasing is live RIGHT NOW — the automation works before the
    # polisher has looked at it. Upgrading the wording is a durable background
    # job (Rule 7), never something the create call waits on: a model round-trip
    # inside this request would make "add automation" hang on the provider, and
    # a provider outage would mean no automation at all rather than a plainly
    # worded one.
    if spec.get("intent_status") == "raw":
        await _queue_polish(model)
    return _as_dict(row)


def composer_intent(spec: dict) -> str:
    """The assembled instruction n8n fires with — what goes in `Automation.intent`
    and, identically, into the workflow's callback body."""
    if spec.get("template"):
        return templates.build_intent(spec)
    return spec.get("intent", "")


async def _queue_polish(model: str) -> None:
    """Ask the queue to upgrade every raw instruction. Best-effort: a queue that
    refuses must never cost the owner the automation he just created.

    One job sweeps every raw automation, so the queue's dedupe-by-kind is the
    right behaviour rather than a limitation — a second enqueue while one is
    pending would only repeat the same sweep."""
    if not model:
        return
    try:
        from app.services.task_queue import enqueue_one

        await enqueue_one(kind=POLISH_JOB, user_id=1, model=model, request_id="")
    except Exception as exc:  # noqa: BLE001
        logger.warning("automation_polish_enqueue_failed", extra={"error": str(exc)})


async def update_automation(
    automation_id: int, changes: dict, db: AsyncSession, model: str = ""
) -> dict:
    """Edit a live automation in place: re-validate, recompose, PUT the new JSON
    over the SAME n8n workflow, and persist.

    Updating beats delete-and-recreate for one reason that matters at 08:00 —
    the workflow id survives, so `last_fired_at`, the execution history and the
    gate node's static data (the "already fired today" memory) all stay put. A
    recreated workflow forgets it already ran and fires a second time.

    `changes` may carry any of: name, agent_id, schedule, instruction, options,
    every_minutes, max_asks, day_flags, url, look_for, domain, recipient,
    interval_minutes, voice. Anything absent keeps its current value.
    """
    row = await db.get(Automation, automation_id)
    if row is None:
        raise ValueError(f"No automation with id {automation_id}.")

    spec = json.loads(row.spec or "{}")
    agent_id = str(changes.get("agent_id") or row.agent_id)

    for field in ("name", "schedule", "instruction", "options", "every_minutes",
                  "max_asks", "day_flags", "url", "look_for", "domain",
                  "recipient", "interval_minutes", "voice"):
        if field in changes and changes[field] is not None:
            spec[field] = changes[field]

    # An edited instruction is the owner's words again, so it goes back through
    # the polisher — otherwise his rewrite would sit beneath a polished version
    # of the sentence he just replaced.
    if "instruction" in changes and changes["instruction"] is not None:
        spec["instruction_raw"] = changes["instruction"]
        spec["intent_status"] = "raw"

    # A schedule that no longer fires once must lose the expiry the old one
    # implied, or a briefing switched from one-off to daily dies after a day.
    if "schedule" in changes:
        spec.pop("expires_at", None)

    spec = _prepare(spec)
    await push_to_n8n(spec, agent_id, row.n8n_workflow_id)

    row.agent_id = agent_id
    row.name = spec.get("name") or row.name
    row.kind = spec["kind"]
    row.intent = composer_intent(spec)
    row.spec = json.dumps(spec)
    row.expires_at = (
        datetime.fromisoformat(spec["expires_at"]) if spec.get("expires_at") else None
    )
    await db.commit()
    await db.refresh(row)
    logger.info("automation_updated", extra={"automation_id": row.id})
    if spec.get("intent_status") == "raw":
        await _queue_polish(model)
    return _as_dict(row)


async def list_automations(db: AsyncSession) -> list[dict]:
    """All automations, newest first. Lazily deactivates anything past its
    expiry (both locally and in n8n) so the list always tells the truth."""
    rows = (
        (await db.execute(select(Automation).order_by(Automation.created_at.desc())))
        .scalars().all()
    )
    n8n = N8nClient()
    dirty = False
    for a in rows:
        expires = a.expires_at
        if expires is not None and expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if a.active and expires and expires < _now():
            a.active = False
            dirty = True
            if a.n8n_workflow_id:
                await n8n.set_active(a.n8n_workflow_id, False)
            logger.info("automation_expired", extra={"automation_id": a.id})
    if dirty:
        await db.commit()
    return [_as_dict(a) for a in rows]


async def set_automation_active(automation_id: int, active: bool, db: AsyncSession) -> dict:
    row = await db.get(Automation, automation_id)
    if row is None:
        raise ValueError(f"No automation with id {automation_id}.")
    if row.n8n_workflow_id:
        ok = await N8nClient().set_active(row.n8n_workflow_id, active)
        if not ok:
            raise ValueError("n8n did not accept the change — is it running?")
    row.active = active
    await db.commit()
    return _as_dict(row)


async def delete_automation(automation_id: int, db: AsyncSession) -> dict:
    row = await db.get(Automation, automation_id)
    if row is None:
        raise ValueError(f"No automation with id {automation_id}.")
    if row.n8n_workflow_id:
        await N8nClient().delete_workflow(row.n8n_workflow_id)  # best-effort
    snapshot = _as_dict(row)
    await db.delete(row)
    await db.commit()
    logger.info("automation_deleted", extra={"automation_id": automation_id})
    return snapshot


async def test_fire(
    automation_id: int, db: AsyncSession, *, profiles,
    orchestrator, turns, session_manager, telegram_bots,
    agent_proxy=None, ws_manager=None,
) -> dict:
    """Fire an automation's stored intent RIGHT NOW — the exact turn n8n would
    start when its schedule comes due (core.trigger_runner.start_trigger_turn),
    never a mock: a push automation really pushes to Telegram, a proactive ask
    really nags with real buttons. Bypasses n8n entirely, so a test never
    touches the workflow's own "already fired today" latch and can never
    cause — or be mistaken for — a duplicate real firing.

    Shared by the Settings "Test" button (routers/automations.py) and the
    agent tool's action='test' (skills/automations.py) so the two paths can
    never drift. `profiles`/`orchestrator`/`turns`/… are late-bound engine
    refs the caller holds (app.state for the router; a `wire()` call for the
    skill, since Tier-1 skills register before the engine exists) — this
    module owns none of them (Rule 6).

    Raises ValueError on a bad id, an unregistered agent, or a full turn
    registry — the same convention every other function here uses, so every
    caller catches ValueError alike.
    """
    row = await db.get(Automation, automation_id)
    if row is None:
        raise ValueError(f"No automation with id {automation_id}.")
    profile = profiles.get(row.agent_id) if profiles else None
    if profile is None:
        raise ValueError(f"Agent '{row.agent_id}' is not registered.")

    from app.core.trigger_runner import start_trigger_turn

    try:
        spec = json.loads(row.spec or "{}")
    except ValueError:
        spec = {}
    output_mode = templates.output_mode(spec.get("template") or "")

    request_id = str(uuid.uuid4())
    started, session_id = await start_trigger_turn(
        db=db,
        profile=profile,
        payload={
            "type": row.kind,
            # Distinct from the real "automation_fired" n8n sends, so a log or
            # a support conversation can tell a manual test from a real firing
            # at a glance — nothing downstream branches on this value.
            "event": "automation_test",
            "automation": row.name,
            "intent": row.intent,
            # Must match what composer._callback_body bakes in for a REAL
            # firing, or a test of a "reply as voice" automation silently
            # tests the WRONG delivery path — exactly the bug this fixes:
            # push-only, same as composer.py's own `mode == "push"` gate.
            "voice": bool(spec.get("voice")) and output_mode == "push",
        },
        output_mode=output_mode,
        request_id=request_id,
        orchestrator=orchestrator,
        turns=turns,
        session_manager=session_manager,
        telegram_bots=telegram_bots,
        agent_proxy=agent_proxy,
        ws_manager=ws_manager,
        triggered_by="n8n",
    )
    if started is None:
        raise ValueError("Too many turns are running at once — retry shortly.")
    logger.info(
        "automation_test_fired",
        extra={"automation_id": automation_id, "agent_id": row.agent_id, "request_id": request_id},
    )
    return {"started": True, "request_id": request_id, "session_id": session_id, "agent_id": row.agent_id}


async def mark_fired(automation_name: str, db: AsyncSession) -> None:
    """Stamp last_fired_at when a trigger arrives carrying this automation's
    name. Best-effort — a miss must never break delivery."""
    try:
        row = (
            (await db.execute(select(Automation).where(Automation.name == automation_name)))
            .scalars().first()
        )
        if row:
            row.last_fired_at = _now()
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("mark_fired_failed", extra={"automation": automation_name, "error": str(exc)})
