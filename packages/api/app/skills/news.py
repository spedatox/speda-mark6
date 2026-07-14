"""
News desk skills (Tier 1 read + Tier 2 analyst).

Four tools over the two-tier news architecture:
  - news_headlines  — read the always-on RSS store (Tier 1, free)
  - news_watch      — manage the breaking-news keyword watchlist
  - news_deep_dive  — NewsData.io corroboration/history (Tier 2, quota-budgeted)
  - read_article    — free full-text extraction from an article URL (trafilatura)

The design intent — encoded in the descriptions so the model routes correctly —
is: stay on Tier 1 (news_headlines, read_article) whenever possible; spend the
Tier-2 budget only for corroboration, related-story timelines, historical search
or structured category queries that RSS cannot answer.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from app.config import settings
from app.core.context import AgentContext
from app.database import AsyncSessionLocal
from app.models.news_item import NewsItem
from app.models.news_quota import NewsQuota
from app.models.news_watch import NewsWatch
from app.news.dedup import normalize_text
from app.skills.base import Skill

logger = logging.getLogger(__name__)

_NEWSDATA_BASE = "https://newsdata.io/api/1"
_TIMEOUT = httpx.Timeout(20.0, connect=8.0)


# ── Tier 1: read the RSS store ───────────────────────────────────────────────

class NewsHeadlinesSkill(Skill):
    name = "news_headlines"
    description = (
        "Reads SPEDA's always-on Turkish/English news store — the deduplicated "
        "RSS headlines collected from NTV, Hürriyet, Sabah, Milliyet and other "
        "outlets, at zero API cost. Use it for the daily briefing, for 'bugün ne "
        "oldu?' / 'what's the latest on X' questions, and as the FIRST stop before "
        "ever spending a Tier-2 news_deep_dive call. Do NOT use it for historical "
        "search beyond the retention window or for structured cross-outlet "
        "analysis — that is news_deep_dive's job. Returns recent headlines newest-"
        "first with outlet, category, a one-line summary, and how many other "
        "outlets ran the same story (a free corroboration signal); flagged items "
        "(matched a watched keyword) are marked."
    )
    read_only = True
    input_schema = {
        "type": "object",
        "properties": {
            "since_hours": {"type": "integer", "description": "Look-back window in hours (default 24).", "default": 24},
            "category": {"type": "string", "description": "Optional outlet category filter, e.g. 'ekonomi', 'dunya', 'teknoloji'."},
            "flagged_only": {"type": "boolean", "description": "Only headlines that matched a watched keyword.", "default": False},
            "limit": {"type": "integer", "description": "Max headlines (default 40, cap 120).", "default": 40},
        },
        "required": [],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        since_hours = int(args.get("since_hours", 24) or 24)
        limit = min(max(int(args.get("limit", 40) or 40), 1), 120)
        category = (args.get("category") or "").strip() or None
        flagged_only = bool(args.get("flagged_only", False))
        cutoff = datetime.utcnow() - timedelta(hours=max(1, since_hours))

        async with AsyncSessionLocal() as db:
            stmt = (
                select(NewsItem)
                .where(NewsItem.fetched_at >= cutoff)
                .order_by(NewsItem.flagged.desc(), NewsItem.fetched_at.desc())
                .limit(limit)
            )
            if category:
                stmt = stmt.where(NewsItem.category == category)
            if flagged_only:
                stmt = stmt.where(NewsItem.flagged.is_(True))
            rows = list((await db.execute(stmt)).scalars().all())

        if not rows:
            return (
                f"No stored headlines in the last {since_hours}h"
                + (f" for category '{category}'" if category else "")
                + ". The collector may not have run yet, or the filter is too narrow."
            )

        lines = [f"NEWS STORE — {len(rows)} headlines (last {since_hours}h, flagged first):"]
        for r in rows:
            try:
                also = json.loads(r.also_in or "[]")
            except (json.JSONDecodeError, TypeError):
                also = []
            corro = f" · also in {len(also)} more outlet(s)" if also else ""
            flag = f" ⚑{r.flagged_keyword}" if r.flagged else ""
            cat = f"/{r.category}" if r.category else ""
            lines.append(f"- [{r.outlet}{cat}]{flag} {r.title}{corro}\n  {r.url}")
        return "\n".join(lines)


# ── Tier 1: manage the watchlist ─────────────────────────────────────────────

class NewsWatchSkill(Skill):
    name = "news_watch"
    description = (
        "Manages the breaking-news keyword watchlist — the terms (a company, a "
        "place like 'OSTİM', a topic like 'siber') that get flagged the instant "
        "they appear in the RSS stream and escalated to a push notification. Use "
        "it when the owner says things like 'X geçen haberleri anında bildir' or "
        "'stop tracking Y'. Do NOT use it to search existing news — that is "
        "news_headlines. Actions: 'add' a keyword, 'remove' (deactivate) one, or "
        "'list' the current watchlist with hit counts. Matching is case- and "
        "Turkish-diacritic-insensitive. Returns a confirmation or the current list."
    )
    read_only = False
    input_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["add", "remove", "list"], "description": "What to do with the watchlist."},
            "keyword": {"type": "string", "description": "The keyword for add/remove (ignored for list)."},
        },
        "required": ["action"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        action = (args.get("action") or "").strip().lower()
        keyword = (args.get("keyword") or "").strip()
        async with AsyncSessionLocal() as db:
            if action == "list":
                rows = list((await db.execute(select(NewsWatch).order_by(NewsWatch.id))).scalars().all())
                if not rows:
                    return "The news watchlist is empty. Add a keyword to get flagged the moment it hits the wire."
                out = ["News watchlist:"]
                for w in rows:
                    state = "active" if w.active else "off"
                    out.append(f"- '{w.keyword}' ({state}, {w.hit_count} hits)")
                return "\n".join(out)

            if not keyword:
                return "A 'keyword' is required for add/remove."
            norm = normalize_text(keyword)
            existing = (
                await db.execute(select(NewsWatch).where(NewsWatch.keyword_norm == norm))
            ).scalar_one_or_none()

            if action == "add":
                if existing is not None:
                    existing.active = True
                    existing.keyword = keyword
                    await db.commit()
                    return f"'{keyword}' is now being watched (reactivated). New matching headlines will flag and notify."
                db.add(NewsWatch(keyword=keyword, keyword_norm=norm,
                                 created_by=context.agent_id, active=True))
                await db.commit()
                return f"Now watching '{keyword}'. The next matching headline flags and pushes a flash."

            if action == "remove":
                if existing is None or not existing.active:
                    return f"'{keyword}' was not on the active watchlist."
                existing.active = False
                await db.commit()
                return f"Stopped watching '{keyword}'."

            return "Unknown action. Use 'add', 'remove', or 'list'."


# ── Tier 2: NewsData.io analyst ──────────────────────────────────────────────

async def _quota_take(purpose: str) -> tuple[bool, int, int]:
    """Reserve one Tier-2 request for `purpose` on today's UTC ledger row.
    Returns (allowed, used_after, budget). Increments BEFORE the HTTP call —
    the request is spent upstream even on most 4xx; callers refund on connect
    errors only."""
    budgets = {
        "deep_dive": settings.news_quota_deep_dive,
        "auto_flag": settings.news_quota_auto_flag,
        "digest": settings.news_quota_digest,
    }
    budget = budgets.get(purpose, settings.news_quota_deep_dive)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(NewsQuota).where(NewsQuota.day == day))).scalar_one_or_none()
        if row is None:
            row = NewsQuota(day=day)
            db.add(row)
            await db.flush()
        used = getattr(row, purpose, 0)
        if used >= budget:
            return False, used, budget
        setattr(row, purpose, used + 1)
        await db.commit()
        return True, used + 1, budget


async def _quota_refund(purpose: str) -> None:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(NewsQuota).where(NewsQuota.day == day))).scalar_one_or_none()
        if row is not None:
            setattr(row, purpose, max(0, getattr(row, purpose, 0) - 1))
            await db.commit()


def _newsdata_q(query: str) -> str:
    """Shape a natural-language query for NewsData's `q`.

    NewsData treats space-separated words as AND, so a phrase like "Türkiye
    gündem bugün önemli" requires ALL words in one article and matches ~nothing
    (empirically 0). Unless the caller already used NewsData's boolean syntax
    (quotes / OR / AND / NOT), OR-join the words so ANY of them matches — broad
    but non-empty, which is what a digest wants. A single word is passed as-is.
    """
    q = query.strip()
    up = f" {q.upper()} "
    if '"' in q or " OR " in up or " AND " in up or " NOT " in up:
        return q  # caller knows NewsData query syntax — respect it verbatim
    words = q.split()
    return " OR ".join(words) if len(words) > 1 else q


class NewsDeepDiveSkill(Skill):
    name = "news_deep_dive"
    description = (
        "Tier-2 news analyst — queries NewsData.io for structured cross-outlet "
        "coverage that free RSS can't give: corroboration (how many outlets, "
        "which angle), related-story timelines, historical search, and "
        "category/topic filters. Use it sparingly and ONLY when news_headlines and "
        "read_article can't answer — it draws from a strict daily quota (NewsData "
        "free tier, 200/day). Do NOT use it to fetch a single article's text (use "
        "read_article) or for anything already in the RSS store (use "
        "news_headlines). CORRECT USAGE: for a country's general agenda/digest set "
        "'country' (e.g. 'tr') and optionally 'category' with a SHORT or empty "
        "'query' — do NOT pass long sentences. For a specific topic use one or two "
        "keywords (e.g. 'enflasyon'). Multiple words are matched as OR "
        "automatically, so keep queries tight. Returns structured results (title, "
        "source, date, description, link)."
    )
    read_only = True
    requires_network = True
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search terms / topic."},
            "purpose": {
                "type": "string",
                "enum": ["deep_dive", "auto_flag", "digest"],
                "description": "Which quota bucket: 'deep_dive' (owner ad-hoc), 'auto_flag' (flagged-story corroboration), 'digest' (daily topic digest). Default 'deep_dive'.",
                "default": "deep_dive",
            },
            "category": {"type": "string", "description": "Optional NewsData category, e.g. 'top', 'business', 'politics', 'technology', 'world'."},
            "country": {"type": "string", "description": "Optional 2-letter country code, e.g. 'tr' for Turkey, 'us'. Strongly improves relevance for a country's news; use it for broad/agenda queries."},
            "language": {"type": "string", "description": "Language code (default 'tr'; use 'en' for English).", "default": "tr"},
            "from_date": {"type": "string", "description": "Optional YYYY-MM-DD lower bound (uses the archive endpoint)."},
            "to_date": {"type": "string", "description": "Optional YYYY-MM-DD upper bound (archive endpoint)."},
        },
        "required": ["query"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        query = (args.get("query") or "").strip()
        if not query:
            return "news_deep_dive: a 'query' is required."
        if not settings.newsdata_api_key:
            return (
                "news_deep_dive is unavailable — no NewsData.io API key is configured "
                "(NEWSDATA_API_KEY). Use news_headlines for the RSS store instead."
            )
        purpose = (args.get("purpose") or "deep_dive").strip()
        if purpose not in ("deep_dive", "auto_flag", "digest"):
            purpose = "deep_dive"

        allowed, used, budget = await _quota_take(purpose)
        if not allowed:
            fallback = await _tier1_fallback(query)
            return (
                f"Tier-2 quota for '{purpose}' is spent for today ({used}/{budget}). "
                f"The budget resets at UTC midnight. Here is what Tier 1 already holds:\n\n{fallback}"
            )

        archive = bool(args.get("from_date") or args.get("to_date"))
        endpoint = "archive" if archive else "latest"
        language = (args.get("language") or "tr").strip()
        country = (args.get("country") or "").strip().lower()
        category = (args.get("category") or "").strip()

        def _params(q: str | None) -> dict:
            p = {"apikey": settings.newsdata_api_key, "language": language}
            if q:
                p["q"] = q                       # OR-shaped by _newsdata_q
            if country:
                p["country"] = country
            if category:
                p["category"] = category
            if args.get("from_date"):
                p["from_date"] = str(args["from_date"]).strip()
            if args.get("to_date"):
                p["to_date"] = str(args["to_date"]).strip()
            return p

        async def _fetch(p: dict):
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                return await client.get(f"{_NEWSDATA_BASE}/{endpoint}", params=p)

        try:
            resp = await _fetch(_params(_newsdata_q(query)))
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            await _quota_refund(purpose)   # never left the client — refund the reservation
            return f"news_deep_dive: could not reach NewsData.io ({type(e).__name__}). Quota not charged."
        except Exception as e:  # noqa: BLE001
            return f"news_deep_dive: request failed ({type(e).__name__}: {e})."

        if resp.status_code == 429:
            return "news_deep_dive: NewsData.io rate-limited the request (upstream 429). Try again later or rely on news_headlines."
        if resp.status_code != 200:
            return f"news_deep_dive: NewsData.io returned HTTP {resp.status_code}."

        results = resp.json().get("results") or []

        # Broad fallback: a keyword query with a country/category context but no
        # hits → drop the query and return that country/topic stream, so the agent
        # gets real material instead of nothing. One extra call, only on empty.
        broadened = False
        if not results and (country or category):
            try:
                resp2 = await _fetch(_params(None))
                if resp2.status_code == 200:
                    results = resp2.json().get("results") or []
                    broadened = bool(results)
            except Exception:  # noqa: BLE001 — fallback is best-effort
                pass

        if not results:
            hint = "" if (country or category) else " (tip: pass country=tr for Turkish news)"
            return (
                f"news_deep_dive: no NewsData results for '{query}'{hint}. "
                f"({used}/{budget} of the '{purpose}' budget used today.)"
            )

        scope = f"{country or category} stream" if broadened else f"'{query}'"
        note = f" — no exact match for '{query}', showing the {scope}" if broadened else ""
        lines = [f"NewsData.io — {len(results)} results for {scope}{note} ({used}/{budget} '{purpose}' budget used today):"]
        for a in results[:12]:
            src = a.get("source_id") or a.get("source_name") or "?"
            date = a.get("pubDate") or ""
            desc = (a.get("description") or "").strip().replace("\n", " ")
            lines.append(
                f"- [{src}] {a.get('title', '(no title)')} ({date})\n"
                f"  {desc[:220]}\n  {a.get('link', '')}"
            )
        return "\n".join(lines)


async def _tier1_fallback(query: str) -> str:
    """When Tier-2 is exhausted, surface matching Tier-1 headlines so the answer
    degrades gracefully instead of going dark."""
    norm = normalize_text(query)
    async with AsyncSessionLocal() as db:
        rows = list((await db.execute(
            select(NewsItem).order_by(NewsItem.fetched_at.desc()).limit(120)
        )).scalars().all())
    hits = [r for r in rows if norm in normalize_text(f"{r.title} {r.summary}")][:10]
    if not hits:
        return "(no matching headlines in the RSS store either)"
    return "\n".join(f"- [{r.outlet}] {r.title}\n  {r.url}" for r in hits)


# ── Tier 1: free full-text extraction ────────────────────────────────────────

class ReadArticleSkill(Skill):
    name = "read_article"
    description = (
        "Fetches a news article URL and extracts its clean, readable main text "
        "for free (no API budget) using a readability extractor. Use it whenever "
        "you have a headline's link from news_headlines and need the actual "
        "article body — this is the preferred, zero-cost way to read a story and "
        "should be chosen over news_deep_dive when you only need one article's "
        "content. Do NOT use it for structured cross-outlet analysis or search "
        "(that is news_deep_dive) or for arbitrary non-article pages. Returns the "
        "extracted title and body text, truncated for context; on a paywall or a "
        "JavaScript-only page that can't be extracted it says so and returns the "
        "link rather than failing."
    )
    read_only = True
    requires_network = True
    input_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The article URL to read (from a news_headlines result)."},
            "max_chars": {"type": "integer", "description": "Max characters of body text to return (default 6000).", "default": 6000},
        },
        "required": ["url"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        url = (args.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            return "read_article: a valid http(s) URL is required."
        max_chars = min(max(int(args.get("max_chars", 6000) or 6000), 500), 20000)
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True,
                                        headers={"User-Agent": "SPEDA-Mark-VI/1.0"}) as client:
                resp = await client.get(url)
                resp.raise_for_status()
        except Exception as e:  # noqa: BLE001
            return f"read_article: could not fetch the page ({type(e).__name__}: {e}). Link: {url}"

        import trafilatura

        # trafilatura is synchronous and CPU-bound — keep it off the event loop.
        text = await asyncio.to_thread(
            trafilatura.extract, resp.text, url=url, include_comments=False, favor_recall=True,
        )
        if not text or len(text.strip()) < 80:
            return (
                "read_article: couldn't extract readable text (likely a paywall or a "
                f"JavaScript-rendered page). Here is the link to open directly: {url}"
            )
        body = text.strip()
        truncated = len(body) > max_chars
        return f"Article text from {url}:\n\n{body[:max_chars]}" + ("\n\n[…truncated]" if truncated else "")
