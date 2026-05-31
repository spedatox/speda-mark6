import json
import logging

from fastapi import BackgroundTasks
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.memory import Memory
from app.models.message import Message
from app.models.session import Session

logger = logging.getLogger(__name__)

# ── Prompts ───────────────────────────────────────────────────────────────────

_EXTRACT_PROMPT = """\
Review the following conversation exchange and extract up to 5 facts worth \
remembering about the user — preferences, ongoing projects, tools they use, \
people they mention, or any explicit instructions for future interactions.

Only extract facts that are genuinely useful to recall later. If the exchange \
contains nothing worth remembering, return an empty array.

Return ONLY a valid JSON array of short strings. No explanation, no markdown.
Example: ["User prefers briefings at 8 AM", "User is building SPEDA on Contabo"]

USER: {user_message}
ASSISTANT: {assistant_message}"""

_TITLE_PROMPT = """\
Generate a short title (3-6 words) for this conversation.
Return ONLY the title — no punctuation, no quotes, nothing else.

USER: {user_message}
ASSISTANT: {assistant_message}"""

_CONSOLIDATE_PROMPT = """\
You are a memory deduplicator. Below is a list of facts about a user.
Many facts may be redundant, outdated, or near-duplicates.

Your job: merge and deduplicate them into a clean, canonical list.
- Keep the most specific and accurate version of each fact.
- Remove direct duplicates and near-duplicates.
- Remove facts that are clearly superseded by newer ones.
- Never invent new facts.
- Return at most 60 facts.

Return ONLY a valid JSON array of short strings. No explanation, no markdown.

FACTS:
{facts}"""


# ── Recall (the read half) ────────────────────────────────────────────────────

async def recall_memories(db: AsyncSession, user_id: int, limit: int = 80) -> list[str]:
    """
    Load this user's stored memories from the DB.
    Returns a deduplicated list of fact strings, most recent first.
    Fast path — no LLM call, runs synchronously before the first Claude call.
    """
    result = await db.execute(
        select(Memory)
        .where(Memory.user_id == user_id)
        .order_by(Memory.created_at.desc())
        .limit(limit)
    )
    rows = result.scalars().all()
    if not rows:
        return []

    # Cheap exact-dedup: preserve order, skip byte-identical duplicates
    seen: set[str] = set()
    facts: list[str] = []
    for row in rows:
        normalised = row.content.strip().lower()
        if normalised not in seen:
            seen.add(normalised)
            facts.append(row.content.strip())

    return facts


def build_memory_block(facts: list[str]) -> str:
    """
    Format recalled facts as a system-prompt section.
    Returns an empty string if there are no facts (no section injected).
    """
    if not facts:
        return ""
    lines = "\n".join(f"- {f}" for f in facts)
    return f"## What you know about the owner\n\n{lines}"


# ── Extraction (the write half) ───────────────────────────────────────────────

async def _load_last_exchange(db: AsyncSession, session_id: int) -> tuple[str, str]:
    """Return (last_user_message, last_assistant_message) for a session."""
    result = await db.execute(
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at.desc())
        .limit(10)
    )
    messages = list(reversed(result.scalars().all()))

    user_msg = assistant_msg = ""
    for m in reversed(messages):
        if not assistant_msg and m.role == "assistant":
            content = m.content
            if isinstance(content, list):
                parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                assistant_msg = " ".join(parts)
            else:
                assistant_msg = str(content)
        elif not user_msg and m.role == "user":
            content = m.content
            if isinstance(content, list):
                parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                user_msg = " ".join(parts)
            else:
                user_msg = str(content)
        if user_msg and assistant_msg:
            break

    return user_msg, assistant_msg


async def _should_consolidate(db: AsyncSession, user_id: int, threshold: int = 40) -> bool:
    """Return True if the user has enough memories to warrant a consolidation pass."""
    result = await db.execute(
        select(Memory).where(Memory.user_id == user_id)
    )
    return len(result.scalars().all()) >= threshold


async def _consolidate_memories(db: AsyncSession, user_id: int, model: str) -> None:
    """
    Run a Haiku consolidation pass: load all facts, ask the model to merge
    near-duplicates, replace the table rows with the cleaned list.
    Runs inside extract_memory when count crosses the threshold.
    """
    from app.services.anthropic_client import AnthropicClient

    result = await db.execute(
        select(Memory).where(Memory.user_id == user_id).order_by(Memory.created_at.asc())
    )
    all_facts = [row.content for row in result.scalars().all()]
    if not all_facts:
        return

    facts_text = json.dumps(all_facts, ensure_ascii=False)
    client = AnthropicClient()
    response = await client.create_message(
        model=model,
        system="You are a precise memory deduplicator. Follow instructions exactly.",
        messages=[{
            "role": "user",
            "content": _CONSOLIDATE_PROMPT.format(facts=facts_text[:6000]),
        }],
        max_tokens=2048,
    )

    raw = response.content[0].text.strip() if response.content else "[]"
    try:
        consolidated: list[str] = json.loads(raw)
        if not isinstance(consolidated, list):
            return
    except json.JSONDecodeError:
        logger.warning("memory_consolidate_parse_error", extra={"raw": raw[:200]})
        return

    # Replace all memories for this user with the consolidated set
    await db.execute(delete(Memory).where(Memory.user_id == user_id))
    for fact in consolidated:
        if isinstance(fact, str) and fact.strip():
            db.add(Memory(user_id=user_id, content=fact.strip()[:512], source_session_id=None))
    await db.commit()

    logger.info(
        "memory_consolidated",
        extra={"user_id": user_id, "before": len(all_facts), "after": len(consolidated)},
    )


async def extract_memory(
    session_id: int,
    request_id: str,
    user_id: int,
    model: str,
) -> None:
    """
    Background task: extract facts from the last exchange and persist to memories table.
    Triggers a consolidation pass when the memory table grows past the threshold.
    Creates its own DB session — never reuses the request session.
    """
    from app.services.anthropic_client import AnthropicClient

    logger.info(
        "memory_extract_start",
        extra={"request_id": request_id, "session_id": session_id},
    )

    try:
        async with AsyncSessionLocal() as db:
            user_msg, assistant_msg = await _load_last_exchange(db, session_id)

            if not user_msg or not assistant_msg:
                return

            client = AnthropicClient()
            response = await client.create_message(
                model=model,
                system="You are a precise memory extractor. Follow instructions exactly.",
                messages=[{
                    "role": "user",
                    "content": _EXTRACT_PROMPT.format(
                        user_message=user_msg[:2000],
                        assistant_message=assistant_msg[:2000],
                    ),
                }],
                max_tokens=512,
            )

            raw = response.content[0].text.strip() if response.content else "[]"

            try:
                facts: list[str] = json.loads(raw)
                if not isinstance(facts, list):
                    facts = []
            except json.JSONDecodeError:
                logger.warning(
                    "memory_extract_parse_error",
                    extra={"request_id": request_id, "raw": raw[:200]},
                )
                return

            for fact in facts:
                if isinstance(fact, str) and fact.strip():
                    db.add(Memory(
                        user_id=user_id,
                        content=fact.strip()[:512],
                        source_session_id=session_id,
                    ))

            await db.commit()
            logger.info(
                "memory_extract_done",
                extra={
                    "request_id": request_id,
                    "session_id": session_id,
                    "facts_stored": len(facts),
                },
            )

            # Trigger consolidation if memory is getting crowded
            if await _should_consolidate(db, user_id):
                logger.info("memory_consolidate_triggered", extra={"user_id": user_id})
                await _consolidate_memories(db, user_id, model)

    except Exception as e:
        logger.error(
            "memory_extract_error",
            extra={"request_id": request_id, "error": str(e)},
        )


# ── Title generation ──────────────────────────────────────────────────────────

async def generate_title(
    session_id: int,
    request_id: str,
    model: str,
) -> None:
    """
    Background task: generate a short session title after the first exchange.
    Only runs if the session has no title yet — idempotent.
    Creates its own DB session.
    """
    from app.services.anthropic_client import AnthropicClient

    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Session).where(Session.id == session_id))
            session = result.scalar_one_or_none()

            if session is None or session.title is not None:
                return

            user_msg, assistant_msg = await _load_last_exchange(db, session_id)
            if not user_msg or not assistant_msg:
                return

            client = AnthropicClient()
            response = await client.create_message(
                model=model,
                system="You generate short conversation titles. Follow instructions exactly.",
                messages=[{
                    "role": "user",
                    "content": _TITLE_PROMPT.format(
                        user_message=user_msg[:500],
                        assistant_message=assistant_msg[:500],
                    ),
                }],
                max_tokens=32,
            )

            title = response.content[0].text.strip() if response.content else ""
            title = title.strip('"\'').strip()[:255]

            if title:
                session.title = title
                await db.commit()
                logger.info(
                    "title_generated",
                    extra={"request_id": request_id, "session_id": session_id, "title": title},
                )

    except Exception as e:
        logger.error(
            "title_generate_error",
            extra={"request_id": request_id, "error": str(e)},
        )


def schedule_background_tasks(
    background_tasks: BackgroundTasks,
    session_id: int,
    request_id: str,
    user_id: int,
    model: str,
) -> None:
    """
    Schedule memory extraction and title generation after an SSE stream completes.
    Both tasks are self-contained — they open their own DB sessions.
    """
    background_tasks.add_task(extract_memory, session_id, request_id, user_id, model)
    background_tasks.add_task(generate_title, session_id, request_id, model)
