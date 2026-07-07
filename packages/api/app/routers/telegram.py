"""
Telegram ingress + linking endpoints.

Zero business logic (CLAUDE.md Rule 1): the webhook validates Telegram's secret
token, acks 200 immediately, and hands the raw update to the gateway as a
background task (Telegram retries aggressively on a slow response, and Rule 7
forbids blocking work in a handler). The link endpoint just returns a deep link.

The webhook path is exempt from X-API-Key (AuthMiddleware) — Telegram can't
attach a header we mint — and is instead guarded by the
X-Telegram-Bot-Api-Secret-Token header set at setWebhook time, compared in
constant time. The gateway additionally drops any sender that isn't the owner.
"""

import hmac
import logging

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse

from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["telegram"])


@router.post("/telegram/webhook/{agent_id}")
async def telegram_webhook(agent_id: str, request: Request, background_tasks: BackgroundTasks):
    """Inbound update for a specific agent's bot (production webhook mode)."""
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not settings.telegram_webhook_secret or not hmac.compare_digest(
        secret, settings.telegram_webhook_secret
    ):
        logger.warning("telegram_webhook_bad_secret", extra={"agent_id": agent_id})
        return JSONResponse(status_code=403, content={"detail": "forbidden"})

    gateway = getattr(request.app.state, "telegram_gateway", None)
    if gateway is None:
        return JSONResponse(status_code=503, content={"detail": "telegram channel offline"})

    try:
        update = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse(status_code=400, content={"detail": "bad json"})

    # Ack immediately; process out of band.
    background_tasks.add_task(gateway.handle_update, agent_id, update)
    return {"ok": True}


@router.get("/telegram/link/{agent_id}")
async def telegram_link(agent_id: str, request: Request):
    """Deep link for pairing an agent's bot: the owner taps it, taps Start, and
    the /start update flows through ingress into the gateway."""
    bots = getattr(request.app.state, "telegram_bots", None)
    if bots is None or not bots.has_own_bot(agent_id):
        return {"error": f"No Telegram bot is configured for '{agent_id}'."}
    bot = bots.get(agent_id)
    link = await bot.connect_deep_link()
    if not link:
        return {"error": "Couldn't reach the Telegram API — check the bot token."}
    from app.core.runtime_state import get_telegram_started

    return {"agent_id": agent_id, "link": link, "linked": agent_id in get_telegram_started()}


@router.get("/telegram/status")
async def telegram_status(request: Request):
    """Channel status for the Settings UI: which bots exist, which are linked,
    and the ingress mode."""
    from app.core.runtime_state import get_telegram_owner_id, get_telegram_started

    bots = getattr(request.app.state, "telegram_bots", None)
    if bots is None:
        return {"configured": False, "mode": settings.telegram_mode, "bots": []}
    started = get_telegram_started()
    return {
        "configured": bots.configured,
        "mode": settings.telegram_mode,
        "owner_linked": bool(get_telegram_owner_id()),
        "bots": [
            {"agent_id": aid, "linked": aid in started}
            for aid in sorted(bots.all_bots())
        ],
    }
