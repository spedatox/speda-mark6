"""
Tier-1 RSS collector — the always-on watcher (no LLM inside).

`poll_all()` is what the n8n clock calls via POST /news/poll: fetch every
enabled feed concurrently, parse, dedup, store new headlines, match them against
the keyword watchlist, and escalate hits to a NightCrawler push turn (cooldowned).
It is pure Python + DB — cheap enough to run every few minutes with no quota
anxiety. Per-feed failures are isolated: one dead outlet never fails the poll.

The orchestrator (LLM) only ever gets involved on a keyword HIT, through the
existing /trigger machinery (app/news/escalate.py) — never per polled item.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from time import mktime

import feedparser
import httpx
from sqlalchemy import delete, select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.news_item import NewsItem
from app.models.news_watch import NewsWatch
from app.news import escalate
from app.news.dedup import canonical_url, normalize_text, title_hash
from app.news.feeds import enabled_feeds

logger = logging.getLogger(__name__)

_UA = "SPEDA-Mark-VI/1.0 (news collector)"
_TIMEOUT = httpx.Timeout(10.0, connect=6.0)
_MAX_ITEMS_PER_FEED = 40   # newest N entries per feed — older ones are stale anyway


async def _fetch(client: httpx.AsyncClient, outlet: str, category: str, url: str) -> list[dict]:
    """Fetch + parse one feed. Returns normalized item dicts; [] on any failure
    (logged, never raised — a dead feed must not fail the whole poll)."""
    try:
        resp = await client.get(url, headers={"User-Agent": _UA})
        resp.raise_for_status()
    except Exception as e:  # noqa: BLE001
        logger.warning("news_feed_failed", extra={"outlet": outlet, "url": url, "error": str(e)})
        return []
    # feedparser is synchronous — keep it off the event loop.
    parsed = await asyncio.to_thread(feedparser.parse, resp.content)
    items: list[dict] = []
    for entry in parsed.entries[:_MAX_ITEMS_PER_FEED]:
        link = (getattr(entry, "link", "") or "").strip()
        title = (getattr(entry, "title", "") or "").strip()
        if not link or not title:
            continue
        summary = (getattr(entry, "summary", "") or "").strip()
        published = None
        tstruct = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
        if tstruct:
            try:
                published = datetime.utcfromtimestamp(mktime(tstruct))
            except (ValueError, OverflowError):
                published = None
        items.append({
            "outlet": outlet,
            "category": category,
            "url": canonical_url(link),
            "title": title,
            "summary": summary[:2000],
            "published_at": published,
        })
    return items


async def poll_all(app=None, *, prune: bool = True) -> dict:
    """Run one full poll cycle. `app` (the FastAPI app) is used only to reach
    app.state for escalation services; when None, hits are flagged but no push
    turn fires (safe for tests). Returns stats for the endpoint response."""
    if not settings.news_poll_enabled:
        return {"enabled": False, "fetched": 0, "new": 0, "flagged": 0}

    feeds = enabled_feeds()
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        batches = await asyncio.gather(
            *[_fetch(client, o, c, u) for (o, c, u) in feeds]
        )
    fetched = [item for batch in batches for item in batch]

    new_count = 0
    flagged_count = 0
    async with AsyncSessionLocal() as db:
        watches = await _active_watches(db)
        # Within-batch dedup: first occurrence wins; later outlets fold into also_in.
        seen_urls: set[str] = set()
        seen_hashes: dict[str, NewsItem] = {}

        for item in fetched:
            if item["url"] in seen_urls:
                continue
            seen_urls.add(item["url"])
            th = title_hash(item["title"])

            # Already stored (same URL) → nothing to do.
            existing_url = (
                await db.execute(select(NewsItem).where(NewsItem.url == item["url"]))
            ).scalar_one_or_none()
            if existing_url is not None:
                continue

            # Same story, different outlet (this poll or a prior one) → corroborate.
            row = seen_hashes.get(th)
            if row is None:
                row = (
                    await db.execute(select(NewsItem).where(NewsItem.title_hash == th))
                ).scalar_one_or_none()
            if row is not None:
                _add_also_in(row, item["outlet"])
                await db.commit()
                continue

            # A genuinely new story.
            new_row = NewsItem(
                url=item["url"], title=item["title"], title_hash=th,
                outlet=item["outlet"], category=item["category"],
                summary=item["summary"], published_at=item["published_at"],
                fetched_at=datetime.utcnow(), also_in="[]",
            )
            match = _match_watch(item["title"], item["summary"], watches)
            if match is not None:
                new_row.flagged = True
                new_row.flagged_keyword = match.keyword
                flagged_count += 1
            db.add(new_row)
            await db.commit()
            await db.refresh(new_row)
            new_count += 1

            if match is not None:
                await _maybe_escalate(db, app, match, new_row)

        if prune:
            await _prune(db)

    stats = {"enabled": True, "feeds": len(feeds), "fetched": len(fetched),
             "new": new_count, "flagged": flagged_count}
    logger.info("news_poll_complete", extra=stats)
    return stats


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _active_watches(db) -> list[NewsWatch]:
    return list(
        (await db.execute(select(NewsWatch).where(NewsWatch.active.is_(True)))).scalars().all()
    )


def _match_watch(title: str, summary: str, watches: list[NewsWatch]) -> NewsWatch | None:
    """First active keyword whose normalized form appears in the normalized
    title+summary. Diacritic/case-insensitive via dedup.normalize_text."""
    hay = normalize_text(f"{title} {summary}")
    for w in watches:
        if w.keyword_norm and w.keyword_norm in hay:
            return w
    return None


def _add_also_in(row: NewsItem, outlet: str) -> None:
    try:
        outlets = json.loads(row.also_in or "[]")
    except (json.JSONDecodeError, TypeError):
        outlets = []
    if outlet != row.outlet and outlet not in outlets:
        outlets.append(outlet)
        row.also_in = json.dumps(outlets, ensure_ascii=False)


async def _maybe_escalate(db, app, watch: NewsWatch, item: NewsItem) -> None:
    """Fire a NightCrawler flash for a keyword hit, honouring the per-keyword
    cooldown so a developing story doesn't spam the owner."""
    now = datetime.now(timezone.utc)
    cooldown = timedelta(minutes=settings.news_flash_cooldown_min)
    last = watch.last_hit_at
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    if last is not None and now - last < cooldown:
        logger.info("news_flash_cooldown", extra={"keyword": watch.keyword})
        return
    watch.last_hit_at = now.replace(tzinfo=None)
    watch.hit_count += 1
    await db.commit()
    if app is not None:
        await escalate.fire_news_flash(app, keyword=watch.keyword, item=item)


async def _prune(db) -> None:
    cutoff = datetime.utcnow() - timedelta(days=max(1, settings.news_retention_days))
    await db.execute(delete(NewsItem).where(NewsItem.fetched_at < cutoff))
    await db.commit()
