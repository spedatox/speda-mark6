"""
Embedding indexing for semantic recall.

Two entry points:
  - embed_session_tail(): per-turn hook (called from memory.schedule_background_tasks)
    that embeds whatever new messages in a session don't have a MessageEmbedding
    row yet. Cheap — at most TAIL_LIMIT messages, one batched API call.
  - backfill_embeddings(): one-time job (POST /admin/index-embeddings) that embeds
    every pre-existing message across all of the user's sessions that's missing
    a row. Self-healing and idempotent: it always just processes whatever's
    pending, so it's safe to re-run any time (e.g. after an embed_texts failure).

Unlike history_indexer.py's per-conversation LLM calls, the OpenAI embeddings
endpoint accepts a batch of texts in one request, so throughput comes from
batching rather than concurrency — a simple sequential loop with a short sleep
between batches is enough to stay well under rate limits.
"""

import asyncio
import logging

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.message import Message
from app.models.message_embedding import MessageEmbedding
from app.models.session import Session
from app.services.embeddings import embed_texts

logger = logging.getLogger(__name__)

BATCH_SIZE = 64            # texts per embeddings API call during backfill
MAX_TEXT_CHARS = 2000      # cap per-message text sent to the embedding model
TAIL_LIMIT = 10            # per-turn hook: max pending messages in one session
BACKFILL_BATCH_DELAY = 1.0 # seconds between batch calls during backfill


def _extract_text(content) -> str:
    """Pull plain text out of an Anthropic content block array (or string)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        return " ".join(p for p in parts if p)
    return ""


async def _embed_and_store(db, user_id: int, batch: list[tuple[Message, str, str]]) -> int:
    """batch: (message, agent_id, text) tuples. Embeds + stores in one call."""
    texts = [text for _, _, text in batch]
    try:
        vectors = await embed_texts(texts)
    except Exception as e:
        logger.warning("embed_batch_failed", extra={"error": str(e), "count": len(batch)})
        return 0

    for (message, agent_id, text), vec in zip(batch, vectors):
        db.add(MessageEmbedding(
            message_id=message.id,
            session_id=message.session_id,
            user_id=user_id,
            agent_id=agent_id,
            role=message.role,
            text=text,
            embedding=vec.tobytes(),
        ))
    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        logger.warning("embed_store_failed", extra={"error": str(e), "count": len(batch)})
        return 0
    return len(batch)


def _pending_messages_query(user_id: int):
    """Messages for this user with no MessageEmbedding row yet, oldest first."""
    return (
        select(Message, Session.agent_id)
        .join(Session, Message.session_id == Session.id)
        .outerjoin(MessageEmbedding, MessageEmbedding.message_id == Message.id)
        .where(
            Session.user_id == user_id,
            Message.role.in_(("user", "assistant")),
            MessageEmbedding.id.is_(None),
        )
        .order_by(Message.created_at.asc())
    )


def _rows_to_batch(rows) -> list[tuple[Message, str, str]]:
    batch = []
    for message, agent_id in rows:
        text = _extract_text(message.content).strip()[:MAX_TEXT_CHARS]
        if text:
            batch.append((message, agent_id, text))
    return batch


async def embed_session_tail(session_id: int, request_id: str, user_id: int) -> None:
    """Per-turn hook: embed this session's not-yet-embedded messages (capped)."""
    try:
        async with AsyncSessionLocal() as db:
            stmt = (
                _pending_messages_query(user_id)
                .where(Message.session_id == session_id)
                .limit(TAIL_LIMIT)
            )
            rows = (await db.execute(stmt)).all()
            batch = _rows_to_batch(rows)
            if not batch:
                return
            stored = await _embed_and_store(db, user_id, batch)
            logger.info(
                "embed_session_tail",
                extra={"request_id": request_id, "session_id": session_id, "stored": stored},
            )
    except Exception as e:
        logger.error(
            "embed_session_tail_error",
            extra={"request_id": request_id, "session_id": session_id, "error": str(e)},
        )


async def backfill_embeddings(user_id: int, request_id: str) -> None:
    """One-time (or re-run anytime) job: embed every pending message for a user."""
    try:
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(_pending_messages_query(user_id))).all()
            if not rows:
                logger.info("backfill_embeddings_nothing_to_do", extra={"request_id": request_id})
                return

            logger.info(
                "backfill_embeddings_start",
                extra={"request_id": request_id, "pending": len(rows)},
            )
            total_stored = 0
            for i in range(0, len(rows), BATCH_SIZE):
                batch = _rows_to_batch(rows[i : i + BATCH_SIZE])
                if batch:
                    total_stored += await _embed_and_store(db, user_id, batch)
                if i + BATCH_SIZE < len(rows):
                    await asyncio.sleep(BACKFILL_BATCH_DELAY)

            logger.info(
                "backfill_embeddings_complete",
                extra={"request_id": request_id, "stored": total_stored, "pending": len(rows)},
            )
    except Exception as e:
        logger.error("backfill_embeddings_error", extra={"request_id": request_id, "error": str(e)})
