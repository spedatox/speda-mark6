# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Automations — view/manage Speda's proactive n8n watchers from Settings,
plus the one-time Telegram connect flow. Zero business logic beyond delegation
to automations.manager (Rule 1)."""

import asyncio
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.runtime_state import get_telegram_owner_id
from app.database import get_db
from app.automations import manager

logger = logging.getLogger(__name__)
router = APIRouter(tags=["automations"])


def _speda_bot(request: Request):
    """The bot that fronts the legacy single-bot connect flow — Speda's. May be
    None if no Speda token is configured (the registry only builds bots for
    configured tokens)."""
    bots = getattr(request.app.state, "telegram_bots", None)
    return bots.get("speda") if bots else None


def _polish_model(request: Request, agent_id: str) -> str:
    """The background-tier model the intent polisher runs on for this agent.

    Resolved HERE because this is the layer that has app.state.profiles, and the
    profile is the only thing allowed to name a model (Rule 10). Derived from
    what the agent is actually ROUTED to, so pinning it to another provider
    moves this job with it rather than quietly leaving it on Anthropic.
    """
    try:
        profiles = request.app.state.profiles
        profile = profiles.get(agent_id) or profiles.default
        return profile.background_model(profile.allocate_model("user"))
    except Exception as exc:  # noqa: BLE001 — no model just means "don't polish"
        logger.warning("polish_model_unresolved", extra={"agent": agent_id, "error": str(exc)})
        return ""


def _drain_soon(background: BackgroundTasks) -> None:
    """Work the queue as soon as this response is out.

    The intent polisher is queued for durability, not for timing — n8n's hourly
    /admin/tasks/drain is the safety net, not the schedule. Without this nudge
    an automation created at 08:05 would keep the owner's raw wording until the
    top of the next hour, which reads as the feature being broken.
    """
    from app.services.task_queue import drain

    background.add_task(drain)


@router.get("/automations")
async def list_automations(db: AsyncSession = Depends(get_db)):
    return {"automations": await manager.list_automations(db)}


@router.get("/automations/agents")
async def automation_agents(request: Request):
    """The roster the create/edit form's agent picker offers.

    Session-scope aliases are filtered out the same way the model matrix filters
    them (routers/agents.py): War Room mirrors Speda's brain and is a place a
    conversation happens, not something that should own a 08:00 briefing.
    """
    return {
        "agents": [
            {"agent_id": p.agent_id, "name": p.name, "domain": p.domain}
            for p in request.app.state.profiles.roster()
            if p.dispatch_target
        ]
    }


@router.post("/automations")
async def create_automation(
    body: dict, request: Request, background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Create an automation from the Settings form. The body is the composer
    spec — template, name, schedule block, instruction, and the ask fields for a
    proactive reminder. Validation errors come back as `error` with the field
    and the fix named, because this form is the owner's only feedback channel."""
    agent_id = str(body.get("agent_id") or "speda")
    spec = dict(body.get("spec") or body)
    spec.pop("agent_id", None)
    try:
        created = await manager.create_automation(
            spec, db, agent_id=agent_id, model=_polish_model(request, agent_id)
        )
    except ValueError as exc:
        return {"error": str(exc)}
    _drain_soon(background)
    return created


@router.put("/automations/{automation_id}")
async def update_automation(
    automation_id: int, body: dict, request: Request, background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Edit a live automation. Recomposes and PUTs over the same n8n workflow, so
    the execution history and the gate node's 'already fired today' memory
    survive the edit (see manager.update_automation)."""
    agent_id = str(body.get("agent_id") or "")
    try:
        updated = await manager.update_automation(
            automation_id, body, db,
            model=_polish_model(request, agent_id or "speda"),
        )
    except ValueError as exc:
        return {"error": str(exc)}
    _drain_soon(background)
    return updated


@router.post("/automations/{automation_id}/test")
async def test_automation(automation_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    """Fire an automation's stored intent RIGHT NOW — the exact turn n8n would
    start when its schedule comes due, never a mock. See manager.test_fire,
    which this shares with the agent tool's action='test' so the Settings
    button and an agent-initiated test can never drift apart."""
    try:
        return await manager.test_fire(
            automation_id, db,
            profiles=request.app.state.profiles,
            orchestrator=request.app.state.orchestrator,
            turns=request.app.state.turns,
            session_manager=request.app.state.session_manager,
            telegram_bots=request.app.state.telegram_bots,
            agent_proxy=request.app.state.agent_proxy,
            ws_manager=request.app.state.ws_manager,
        )
    except ValueError as exc:
        return {"error": str(exc)}


@router.get("/automations/drift")
async def workflow_drift():
    """Cheap probe: is n8n running the workflows the repo says it is?

    `drift: []` means everything matches — n8n's gate node stops the branch on
    an empty return, so a clean check costs one HTTP call and zero tokens. A
    non-empty list is worth an actual notification: a shipped workflow whose
    live copy has drifted keeps running silently, which is how a briefing spent
    a week rendering the format its committed intent had already banned.
    """
    from app.services import n8n_drift
    from app.services.n8n_api import N8nClient

    drift = await n8n_drift.scan(N8nClient())
    return {"drift": drift, "in_sync": not drift}


@router.post("/automations/{automation_id}/toggle")
async def toggle_automation(automation_id: int, body: dict, db: AsyncSession = Depends(get_db)):
    try:
        return await manager.set_automation_active(automation_id, bool(body.get("active", True)), db)
    except ValueError as exc:
        return {"error": str(exc)}


@router.delete("/automations/{automation_id}")
async def delete_automation(automation_id: int, db: AsyncSession = Depends(get_db)):
    try:
        return {"deleted": await manager.delete_automation(automation_id, db)}
    except ValueError as exc:
        return {"error": str(exc)}


@router.get("/automations/status")
async def automations_status(request: Request):
    """One call powering the Settings header: is the pipeline ready end-to-end?"""
    n8n_online = False
    telegram = _speda_bot(request)
    from app.services.n8n_api import N8nClient

    n8n = N8nClient()
    if n8n.configured:
        n8n_online = await n8n.ping()
    return {
        "n8n_configured": n8n.configured,
        "n8n_online": n8n_online,
        "n8n_url": settings.n8n_api_url,
        "telegram_configured": bool(telegram and telegram.configured),
        "telegram_connected": bool(get_telegram_owner_id()),
    }


@router.post("/automations/telegram/connect")
async def telegram_connect(request: Request):
    """Return the t.me deep link for Speda's bot and (in 'off' mode) start
    listening for the owner's /start tap. When ingress is running (polling/
    webhook) the /start update is captured by the gateway automatically, so no
    separate capture task is spawned. The UI opens the link, then polls
    /automations/telegram/status until connected."""
    telegram = _speda_bot(request)
    if not telegram or not telegram.configured:
        return {
            "error": "Telegram bot token not set. Create a bot with @BotFather and "
                     "put TELEGRAM_BOT_TOKEN (or TELEGRAM_BOT_TOKEN_SPEDA) in the "
                     "backend .env, then restart."
        }
    link = await telegram.connect_deep_link()
    if not link:
        return {"error": "Could not reach the Telegram API — check the bot token."}
    # The legacy getUpdates capture would fight a running poll loop for updates,
    # so only use it when there is no persistent ingress.
    if settings.telegram_mode.strip().lower() == "off":
        asyncio.create_task(telegram.capture_chat_id(timeout_s=120))
    return {"link": link}


@router.get("/automations/telegram/status")
async def telegram_status(request: Request):
    telegram = _speda_bot(request)
    return {
        "configured": bool(telegram and telegram.configured),
        "connected": bool(get_telegram_owner_id()),
    }
