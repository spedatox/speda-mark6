"""
Keyword-hit escalation — the one place a Tier-1 flag becomes an LLM turn.

When the collector matches a watchlist keyword it calls fire_news_flash(), which
runs NightCrawler (the OSINT / surveillance persona) through its normal
orchestrator loop as a background task and delivers the result as a push — the
same machinery /trigger uses, so there is no HTTP loopback and no new transport.
NightCrawler decides whether the item truly warrants the owner's attention and
may corroborate it with one Tier-2 news_deep_dive before pushing.

Best-effort by contract: any failure here is logged and swallowed — a news flash
must never disturb the poll that spawned it.
"""

import asyncio
import logging

from app.core.context import AgentContext
from app.database import AsyncSessionLocal
from app.models.news_item import NewsItem
from app.schemas.sse import SSEEventType

logger = logging.getLogger(__name__)

_FLASH_AGENT = "nightcrawler"


async def fire_news_flash(app, *, keyword: str, item: NewsItem) -> None:
    """Spawn a NightCrawler push turn for a flagged headline. Returns immediately;
    the turn runs detached. Missing services (early startup) → logged no-op."""
    state = getattr(app, "state", None)
    orchestrator = getattr(state, "orchestrator", None)
    profiles = getattr(state, "profiles", None)
    telegram_bots = getattr(state, "telegram_bots", None)
    if orchestrator is None or profiles is None:
        logger.warning("news_flash_no_engine", extra={"keyword": keyword})
        return
    profile = profiles.get(_FLASH_AGENT)
    if profile is None:
        logger.warning("news_flash_no_agent", extra={"agent": _FLASH_AGENT})
        return

    payload = {
        "type": "news_flash",
        "event": "news_flash",
        "keyword": keyword,
        "item": {"title": item.title, "url": item.url, "outlet": item.outlet},
        "output_mode": "push",
    }
    asyncio.create_task(_run_flash(orchestrator, profiles, telegram_bots, profile, payload))


async def _run_flash(orchestrator, profiles, telegram_bots, profile, payload: dict) -> None:
    import uuid

    from app.core.session_manager import SessionManager  # local import avoids cycle

    request_id = str(uuid.uuid4())
    item = payload["item"]
    keyword = payload["keyword"]
    try:
        async with AsyncSessionLocal() as db:
            session_manager = SessionManager()
            session = await session_manager.get_or_create(
                db=db, user_id=1, triggered_by="n8n",
                model_used=profile.allocate_model("n8n"), agent_id=profile.agent_id,
            )
            context = AgentContext(
                agent_id=profile.agent_id,
                user_id=1,
                session_id=session.id,
                request_id=request_id,
                triggered_by="n8n",
                trigger_payload=payload,
                output_mode="push",
                model=profile.allocate_model("n8n"),
                system_prompt="",
                conversation_history=[{
                    "role": "user",
                    "content": (
                        f"NEWS FLASH — a watched keyword just hit the wire. Keyword: "
                        f"'{keyword}'. Headline: \"{item['title']}\" ({item['outlet']}). "
                        f"Link: {item['url']}. Decide whether this genuinely warrants "
                        "the owner's immediate attention. If it does, optionally "
                        "corroborate it with ONE news_deep_dive (purpose='auto_flag'), "
                        "then compose a short, concrete push — lead with what happened "
                        "and why it matters to the owner. If it does NOT warrant a "
                        "notification, reply with exactly the single word SKIP and "
                        "nothing else."
                    ),
                }],
                db=db,
                timezone="UTC",
            )
            chunks: list[str] = []
            async for event in orchestrator.run(context):
                if event.type == SSEEventType.CHUNK and isinstance(event.data, str):
                    chunks.append(event.data)
            final_text = "".join(chunks).strip()

            # NightCrawler can suppress a low-value flash by answering SKIP.
            if not final_text or final_text.upper().startswith("SKIP"):
                logger.info("news_flash_suppressed", extra={"keyword": keyword})
                return
            if telegram_bots is not None:
                delivered = await telegram_bots.deliver_message(profile.agent_id, final_text)
                logger.info(
                    "news_flash_delivered" if delivered else "news_flash_undelivered",
                    extra={"keyword": keyword, "chars": len(final_text)},
                )
    except Exception as e:  # noqa: BLE001 — a flash must never crash the poller
        logger.error("news_flash_failed", extra={"keyword": keyword, "error": str(e)})
