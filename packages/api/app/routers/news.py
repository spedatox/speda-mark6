"""
News desk router — the n8n poll entrypoint and owner-facing read/CRUD surface.

Thin per Rule 1: the collector (app/news/collector.py) owns all the fetch/dedup/
match logic; this module just authenticates and delegates. POST /news/poll is
the ONLY place the news system is driven by a clock, and that clock is n8n —
there is no internal timer.
"""

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.news_item import NewsItem
from app.models.news_watch import NewsWatch
from app.news import collector
from app.news.dedup import normalize_text
from app.services.n8n import validate_n8n_secret

logger = logging.getLogger(__name__)
router = APIRouter(tags=["news"])


class PollRequest(BaseModel):
    prune: bool = True


class WatchCreate(BaseModel):
    keyword: str
    active: bool = True


@router.post("/news/poll")
async def news_poll(request: Request, body: PollRequest | None = None):
    """Run one Tier-1 collection cycle. Called by n8n on a schedule; requires both
    X-API-Key (middleware) and X-N8N-Secret (this handler), exactly like /trigger.
    Returns poll stats: feeds hit, items fetched, new stored, keywords flagged."""
    validate_n8n_secret(request)
    prune = body.prune if body is not None else True
    stats = await collector.poll_all(request.app, prune=prune)
    return stats


@router.get("/news/items")
async def news_items(
    since_hours: int = 24,
    flagged: bool = False,
    category: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """Recent stored headlines (newest first) — for the UI and debugging. The
    news_headlines skill is the model-facing equivalent."""
    cutoff = datetime.utcnow() - timedelta(hours=max(1, since_hours))
    stmt = (
        select(NewsItem)
        .where(NewsItem.fetched_at >= cutoff)
        .order_by(NewsItem.fetched_at.desc())
        .limit(min(max(limit, 1), 200))
    )
    if flagged:
        stmt = stmt.where(NewsItem.flagged.is_(True))
    if category:
        stmt = stmt.where(NewsItem.category == category)
    rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "id": r.id, "title": r.title, "url": r.url, "outlet": r.outlet,
            "category": r.category, "summary": r.summary,
            "flagged": r.flagged, "flagged_keyword": r.flagged_keyword,
            "also_in": r.also_in, "fetched_at": r.fetched_at.isoformat(),
        }
        for r in rows
    ]


@router.get("/news/watch")
async def list_watches(db: AsyncSession = Depends(get_db)):
    """Every keyword on the watchlist with its hit stats."""
    rows = (await db.execute(select(NewsWatch).order_by(NewsWatch.id))).scalars().all()
    return [
        {
            "id": w.id, "keyword": w.keyword, "active": w.active,
            "created_by": w.created_by, "hit_count": w.hit_count,
            "last_hit_at": w.last_hit_at.isoformat() if w.last_hit_at else None,
        }
        for w in rows
    ]


@router.post("/news/watch")
async def add_watch(body: WatchCreate, db: AsyncSession = Depends(get_db)):
    """Add (or reactivate) a watchlist keyword. Idempotent by normalized form."""
    norm = normalize_text(body.keyword)
    existing = (
        await db.execute(select(NewsWatch).where(NewsWatch.keyword_norm == norm))
    ).scalar_one_or_none()
    if existing is not None:
        existing.active = body.active
        existing.keyword = body.keyword.strip()
        await db.commit()
        return {"id": existing.id, "keyword": existing.keyword, "active": existing.active}
    row = NewsWatch(
        keyword=body.keyword.strip(), keyword_norm=norm,
        created_by="owner", active=body.active,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {"id": row.id, "keyword": row.keyword, "active": row.active}
