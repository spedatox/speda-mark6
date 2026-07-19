"""Automations — view/manage SPEDA's proactive n8n watchers from Settings,
plus the one-time Telegram connect flow. Zero business logic beyond delegation
to automations.manager (Rule 1)."""

import asyncio
import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.runtime_state import get_telegram_owner_id
from app.database import get_db
from app.automations import manager

logger = logging.getLogger(__name__)
router = APIRouter(tags=["automations"])


def _speda_bot(request: Request):
    """The bot that fronts the legacy single-bot connect flow — SPEDA's. May be
    None if no SPEDA token is configured (the registry only builds bots for
    configured tokens)."""
    bots = getattr(request.app.state, "telegram_bots", None)
    return bots.get("speda") if bots else None


@router.get("/automations")
async def list_automations(db: AsyncSession = Depends(get_db)):
    return {"automations": await manager.list_automations(db)}


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
    """Return the t.me deep link for SPEDA's bot and (in 'off' mode) start
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
