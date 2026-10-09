# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

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
    """batch: (message, agent_id, text) tuples. Indexes lexically, then embeds.

    The keyword index is written FIRST and committed on its own, deliberately.
    Recall is hybrid now (see app/services/lexical.py), and the two halves fail
    independently: an OpenAI outage should cost the meaning half of recall for
    those messages, not both halves. Ordering it this way is what makes a
    message searchable by the words in it even on a day the embeddings API is
    unreachable — and the embedding backfill will still find it pending, because
    pending-ness is defined by the MessageEmbedding row, not by this one.
    """
    from app.services import lexical

    for message, _, text in batch:
        if not await lexical.index_message(db, message.id, text):
            if db.get_bind().dialect.name == "sqlite":
                raise RuntimeError("Conversation lexical indexing failed; retry required")
    try:
        await db.commit()
    except Exception as e:  # noqa: BLE001
        await db.rollback()
        logger.warning("lexical_batch_failed", extra={"error": str(e), "count": len(batch)})
        raise

    texts = [text for _, _, text in batch]
    try:
        vectors = await embed_texts(texts)
    except Exception as e:
        logger.warning("embed_batch_failed", extra={"error": str(e), "count": len(batch)})
        raise
    if len(vectors) != len(batch):
        raise ValueError("Incomplete embedding batch; retry required")

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
        raise
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


async def index_saved_message(message_id: int, user_id: int, request_id: str) -> None:
    """Retry an exact saved message's local projection through the existing durable queue."""
    from app.services import lexical
    async with AsyncSessionLocal() as db:
        message = (await db.execute(select(Message).join(Session, Message.session_id == Session.id)
            .where(Message.id == message_id, Session.user_id == user_id))).scalar_one_or_none()
        if message is None:
            return  # An explicitly deleted source needs no search projection.
        if not await lexical.index_message(db, message.id, _extract_text(message.content)):
            raise RuntimeError("Conversation lexical indexing failed; retry required")
        await db.commit()
    logger.info("message_lexical_retry_complete", extra={"request_id": request_id, "message_id": message_id})


async def embed_session_tail(session_id: int, request_id: str, user_id: int) -> None:
    """Per-turn hook: embed this session's not-yet-embedded messages (capped)."""
    try:
        async with AsyncSessionLocal() as db:
            from app.services import lexical
            # Heal local indexing independently of whether a vector already exists.
            messages = (await db.execute(select(Message).where(
                Message.session_id == session_id,
                Message.role.in_(("user", "assistant")),
            ).order_by(Message.id.desc()).limit(BATCH_SIZE))).scalars().all()
            for message in messages:
                if not await lexical.index_message(db, message.id, _extract_text(message.content)):
                    if db.get_bind().dialect.name == "sqlite":
                        raise RuntimeError("Conversation lexical indexing failed; retry required")
            await db.commit()
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
        raise


async def backfill_lexical(user_id: int, request_id: str) -> int:
    """Index saved text independently of embedding coverage, safely on retry."""
    from app.services import lexical

    written = 0
    try:
        async with AsyncSessionLocal() as db:
            if not await lexical.ensure_index(db, lexical.MESSAGES):
                return 0
            known = await lexical.indexed_ids(db, lexical.MESSAGES)
            rows = (
                await db.execute(
                    select(Message.id, Message.content)
                    .join(Session, Message.session_id == Session.id)
                    .where(Session.user_id == user_id, Message.role.in_(("user", "assistant")))
                    .order_by(Message.id.desc())
                )
            ).all()
            missing = [(mid, _extract_text(content)) for mid, content in rows
                       if mid not in known and _extract_text(content).strip()]
            for i, (message_id, body) in enumerate(missing, start=1):
                if not await lexical.index_message(db, message_id, body):
                    raise RuntimeError("Conversation lexical backfill failed; retry required")
                written += 1
                # Commit in chunks: one transaction over tens of thousands of
                # inserts holds the write lock long enough for a live turn's
                # background task to hit the busy timeout.
                if i % 500 == 0:
                    await db.commit()
            await db.commit()
        logger.info(
            "backfill_lexical_complete",
            extra={"request_id": request_id, "indexed": written, "total": len(rows)},
        )
    except Exception as e:  # noqa: BLE001
        logger.error("backfill_lexical_error", extra={"request_id": request_id, "error": str(e)})
        raise
    return written


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
