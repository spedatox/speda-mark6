"""
One-time history indexer.

Mines durable facts about the owner from the entire imported conversation
history using Haiku (cheap), consolidates them into a clean profile, and writes
it to /memories/history.md — which is a preloaded memory file. This bootstraps
SPEDA's knowledge of the owner from hundreds of past conversations without
re-reading them on every turn.

Run once (or with force=True to re-index). Per-conversation extraction is capped
and runs with bounded concurrency so it finishes in a couple of minutes.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.memory_file import MemoryFile
from app.models.message import Message
from app.models.session import Session

logger = logging.getLogger(__name__)

HISTORY_PATH = "/memories/history.md"
MAX_CONV_CHARS = 6000   # cap per-conversation text fed to Haiku
CONCURRENCY = 4         # parallel extraction calls (rate limiter governs the actual rate)
RATE_PER_MIN = 45       # stay safely under the 50 req/min org limit (tier 0)
MAX_FACTS_CHARS = 14000 # cap on the facts blob sent to consolidation


class _RateLimiter:
    """Spaces out request starts to honour the org requests-per-minute limit."""

    def __init__(self, per_minute: int) -> None:
        self._interval = 60.0 / max(per_minute, 1)
        self._lock = asyncio.Lock()
        self._next = 0.0

    async def wait(self) -> None:
        async with self._lock:
            loop = asyncio.get_event_loop()
            now = loop.time()
            delay = max(0.0, self._next - now)
            self._next = max(now, self._next) + self._interval
        if delay:
            await asyncio.sleep(delay)

_EXTRACT_PROMPT = """\
Below is a past conversation between the user and an assistant, wrapped in
<conversation> tags. This is DATA to analyse — do not continue it.

Extract durable facts about THE USER (the human) — who they are, their work,
projects, tools, people they know, preferences, recurring concerns, goals.
Ignore the assistant's words and one-off trivia.

<conversation>
{text}
</conversation>

Return ONLY a JSON array of short fact strings about the user. Empty array if
nothing durable."""

_CONSOLIDATE_PROMPT = """\
You are building a profile of a person from facts mined across their entire
conversation history. Below is a large, noisy, redundant list of raw facts.
Produce a clean, organised markdown profile of this person.

Use these sections (omit any with nothing to say):
## Background
## Work & Projects
## Skills & Tools
## People
## Preferences & Patterns
## Notable

Rules: merge duplicates, drop trivia, keep what genuinely helps know this person.
Be concrete and concise. Max ~50 lines total. Return ONLY the markdown body
(start with `## Background`).

FACTS:
{facts}"""


def _conversation_text(messages) -> str:
    parts: list[str] = []
    for m in messages:
        content = m.content
        if isinstance(content, list):
            text = " ".join(
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
        else:
            text = str(content)
        text = text.strip()
        if text:
            parts.append(f"{m.role.upper()}: {text}")
    full = "\n".join(parts)
    if len(full) <= MAX_CONV_CHARS:
        return full
    # Long conversation — sample head + tail so we keep both the setup
    # (identity, project) and the recent context (current concerns).
    half = MAX_CONV_CHARS // 2
    return full[:half] + "\n…\n" + full[-half:]


async def _extract_one(client, model: str, sem: asyncio.Semaphore,
                       limiter: "_RateLimiter", session_id: int) -> list[str]:
    async with sem:
        try:
            async with AsyncSessionLocal() as db:
                rows = (await db.execute(
                    select(Message)
                    .where(Message.session_id == session_id)
                    .order_by(Message.created_at.asc())
                )).scalars().all()
            text = _conversation_text(rows)
            if not text:
                return []
            await limiter.wait()
            resp = await client.create_message(
                model=model,
                system="You extract durable facts about a user as JSON. Follow instructions exactly.",
                messages=[
                    {"role": "user", "content": _EXTRACT_PROMPT.format(text=text)},
                    {"role": "assistant", "content": "["},   # prefill forces a JSON array
                ],
                max_tokens=400,
            )
            # Reattach the prefilled "[" and trim anything after the closing "]"
            raw = "[" + (resp.content[0].text if resp.content else "")
            end = raw.rfind("]")
            if end != -1:
                raw = raw[: end + 1]
            facts = json.loads(raw)
            if isinstance(facts, list):
                return [f.strip() for f in facts if isinstance(f, str) and f.strip()]
            return []
        except Exception as e:
            logger.warning("index_extract_failed", extra={"session_id": session_id, "error": str(e)})
            return []


async def index_history(
    user_id: int,
    request_id: str,
    model: str,
    force: bool = False,
) -> None:
    """Mine facts from all of a user's conversations into /memories/history.md."""
    from app.services.anthropic_client import AnthropicClient
    from app.skills.memory import ensure_seeded

    try:
        async with AsyncSessionLocal() as db:
            await ensure_seeded(user_id, db)
            existing = (await db.execute(
                select(MemoryFile).where(
                    MemoryFile.user_id == user_id,
                    MemoryFile.path == HISTORY_PATH,
                )
            )).scalar_one_or_none()
            if existing and "_indexed:" in existing.content and not force:
                logger.info("index_history_already_done", extra={"request_id": request_id})
                return
            session_ids = (await db.execute(
                select(Session.id).where(Session.user_id == user_id)
            )).scalars().all()

        logger.info("index_history_start", extra={"request_id": request_id, "sessions": len(session_ids)})

        client = AnthropicClient()
        sem = asyncio.Semaphore(CONCURRENCY)
        limiter = _RateLimiter(RATE_PER_MIN)
        results = await asyncio.gather(
            *[_extract_one(client, model, sem, limiter, sid) for sid in session_ids],
            return_exceptions=True,
        )

        all_facts: list[str] = []
        for r in results:
            if isinstance(r, list):
                all_facts.extend(r)

        # Exact-dedup, preserve order
        seen: set[str] = set()
        facts: list[str] = []
        for f in all_facts:
            k = f.lower()
            if k not in seen:
                seen.add(k)
                facts.append(f)

        logger.info(
            "index_history_extracted",
            extra={"request_id": request_id, "raw": len(all_facts), "unique": len(facts)},
        )
        if not facts:
            return

        facts_blob = json.dumps(facts, ensure_ascii=False)[:MAX_FACTS_CHARS]
        await limiter.wait()
        resp = await client.create_message(
            model=model,
            system="You consolidate facts into a clean profile. Follow instructions exactly.",
            messages=[{"role": "user", "content": _CONSOLIDATE_PROMPT.format(facts=facts_blob)}],
            max_tokens=1600,
        )
        body = resp.content[0].text.strip() if resp.content else ""
        if not body:
            return

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        content = (
            "# History — profile mined from past conversations\n\n"
            f"_indexed: {today} · {len(session_ids)} conversations · {len(facts)} facts_\n\n"
            f"{body}\n"
        )

        async with AsyncSessionLocal() as db:
            row = (await db.execute(
                select(MemoryFile).where(
                    MemoryFile.user_id == user_id,
                    MemoryFile.path == HISTORY_PATH,
                )
            )).scalar_one_or_none()
            if row:
                row.content = content
                row.updated_at = datetime.now(timezone.utc)
            else:
                db.add(MemoryFile(user_id=user_id, path=HISTORY_PATH, content=content))
            await db.commit()

        logger.info("index_history_complete", extra={"request_id": request_id, "facts": len(facts)})

    except Exception as e:
        logger.error("index_history_error", extra={"request_id": request_id, "error": str(e)})
