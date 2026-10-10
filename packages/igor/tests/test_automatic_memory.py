"""Automatic recall uses owner history before a model can request a tool."""
import asyncio
import time
from datetime import datetime, timezone

import numpy as np
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

import app.models
from app.config import settings
from app.core.context import AgentContext
from app.core.session_manager import SessionManager
from app.database import Base
from app.models.message_embedding import MessageEmbedding
from app.models.session import Session
from app.services.relevant_recall import recall_for_turn
from app.skills.memory import MemoryRecallCache
from app.skills.semantic_search import search_conversations


@pytest.fixture
async def db(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(settings, "database_url", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setattr(settings, "recall_translate_queries", False)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def concurrent_db(monkeypatch, tmp_path):
    # A cancelled foreground reader and a detached warmup need independent
    # connections. In-memory SQLite's StaticPool shares one connection, whose
    # cancellation invalidates the other reader; the deployed store is a file.
    url = "sqlite+aiosqlite:///" + (tmp_path / "concurrent-recall.db").as_posix()
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(settings, "database_url", url)
    monkeypatch.setattr(settings, "recall_translate_queries", False)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


async def context(db, text, agent="atomix"):
    session = Session(user_id=1, agent_id=agent, triggered_by="user", model_used="test")
    db.add(session)
    await db.commit()
    return AgentContext(agent, 1, session.id, "automatic-test", "user", {}, "respond", "test", "",
                        [{"role": "user", "content": text}], db, "Europe/Istanbul")


async def past(db, words, *, agent="atomix", owner=1, triggered="user", vector=None):
    session = Session(user_id=owner, agent_id=agent, triggered_by=triggered, model_used="test")
    db.add(session)
    await db.commit()
    messages = []
    for role, text in words:
        message = await SessionManager().save_message(db, session.id, role, text)
        messages.append(message)
        if vector is not None:
            db.add(MessageEmbedding(message_id=message.id, session_id=session.id, user_id=owner,
                agent_id=agent, role=role, text=text, embedding=vector.tobytes()))
    await db.commit()
    return messages


async def test_fresh_atomix_session_has_previous_thread_without_observations_or_embeddings(db, monkeypatch):
    await past(db, [("user", "My sore throat usually starts before a cold."),
                    ("assistant", "Could also be irritation; watch how it develops."),
                    ("user", "We will see tomorrow.")])
    ctx = await context(db, "I was right")
    async def unavailable(texts):
        raise ConnectionError("offline")
    monkeypatch.setattr("app.services.embeddings.embed_texts", unavailable)
    result = await recall_for_turn(ctx, cache=MemoryRecallCache())
    assert "sore throat" in result.conversations
    assert "user message:" in result.conversations
    assert "assistant message:" in result.conversations
    assert result.degraded


async def test_short_followup_uses_latest_owner_activity_in_an_older_session(db):
    from app.services.relevant_recall import recent_exchange
    older = await past(db, [("user", "My sore throat has become a cough.")])
    newer = await past(db, [("user", "I bought a new backpack.")])
    older_session = await db.get(Session, older[0].session_id)
    newer_session = await db.get(Session, newer[0].session_id)
    older_session.started_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    newer_session.started_at = datetime(2026, 10, 1, tzinfo=timezone.utc)
    older[0].created_at = datetime(2026, 10, 9, tzinfo=timezone.utc)
    newer[0].created_at = datetime(2026, 10, 8, tzinfo=timezone.utc)
    await db.commit()
    ctx = await context(db, "I was right")
    text, seed = await recent_exchange(ctx)
    assert "sore throat" in text and "sore throat" in seed
    assert "backpack" not in text


async def test_recent_context_deduplicates_text_but_preserves_execution_receipts(db):
    from app.services.relevant_recall import recent_exchange
    messages = await past(db, [("user", "Please send the final application document."),
        ("assistant", "The document was prepared and delivery was attempted.")])
    messages[1].content = [*messages[1].content, {"type": "_speda_meta", "tools": [{"name": "send_file", "result": "Error: delivery rejected"}]}]
    await db.commit()
    ctx = await context(db, "Was that done?")
    existing = "Please send the final application document. The document was prepared and delivery was attempted."
    text, _ = await recent_exchange(ctx, existing_text=existing)
    assert "Please send the final application document." not in text
    assert "The document was prepared" not in text
    assert "delivery rejected" in text and "assistant message:" in text


async def test_keyword_search_reaches_messages_that_have_never_been_embedded(db):
    messages = await past(db, [("user", "The Aster application deadline is Friday.")])
    ctx = await context(db, "When is the Aster application deadline?")
    text = await search_conversations(ctx, {"query": "Aster application", "_lexical_only": True})
    assert "deadline is Friday" in text
    assert f"message:{messages[0].id}" in text


async def test_paraphrase_reaches_original_exchange_before_first_reply(db, monkeypatch):
    vector = np.array([1., 0., 0.], dtype=np.float32)
    await past(db, [("user", "I prefer explanations that get directly to the point.")], vector=vector)
    ctx = await context(db, "Give me the concise version of the explanation")
    calls = []
    async def embed(texts):
        calls.append(texts)
        return [vector]
    monkeypatch.setattr("app.services.embeddings.embed_texts", embed)
    result = await recall_for_turn(ctx, cache=MemoryRecallCache())
    assert "directly to the point" in result.conversations
    assert len(calls) == 1
    assert not result.degraded


async def test_deadline_keeps_local_matches_and_leaves_chat_transaction_usable(db, monkeypatch):
    await past(db, [("user", "The Aster application deadline is Friday.")])
    ctx = await context(db, "Tell me about the Aster application deadline")
    monkeypatch.setattr(settings, "automatic_recall_timeout_ms", 80)
    async def slow(texts):
        await asyncio.sleep(5)
    monkeypatch.setattr("app.services.embeddings.embed_texts", slow)
    start = time.monotonic()
    result = await recall_for_turn(ctx, cache=MemoryRecallCache())
    assert time.monotonic() - start < .5
    assert "Friday" in result.conversations
    assert result.degraded and ctx.extra["automatic_recall"]["error"] == "deadline"
    assert await db.get(Session, ctx.session_id) is not None


async def test_provider_failure_after_blocking_work_still_records_a_deadline_miss(db, monkeypatch):
    await past(db, [("user", "The Aster application deadline is Friday.")])
    ctx = await context(db, "Tell me about the Aster application deadline")
    monkeypatch.setattr(settings, "automatic_recall_timeout_ms", 60)
    async def blocked_failure(texts):
        # Synchronous provider setup can delay the event loop's timeout callback.
        time.sleep(.08)
        raise ConnectionError("provider setup failed")
    monkeypatch.setattr("app.services.embeddings.embed_texts", blocked_failure)
    cache = MemoryRecallCache()
    result = await recall_for_turn(ctx, cache=cache)
    await cache.close()
    assert "Friday" in result.conversations and result.degraded
    assert ctx.extra["automatic_recall"]["deadline_missed"]


async def test_automatic_history_preserves_owner_agent_and_trigger_scope(db, monkeypatch):
    await past(db, [("user", "Aster other-owner secret")], owner=2)
    await past(db, [("user", "Aster other-agent secret")], agent="ultron")
    await past(db, [("user", "Aster automated instruction")], triggered="n8n")
    await past(db, [("user", "Aster owner conversation")])
    ctx = await context(db, "Tell me about the Aster conversation")
    async def unavailable(texts):
        raise ConnectionError()
    monkeypatch.setattr("app.services.embeddings.embed_texts", unavailable)
    result = await recall_for_turn(ctx, scope="own", cache=MemoryRecallCache())
    assert "owner conversation" in result.conversations
    assert "other-owner" not in result.conversations
    assert "other-agent" not in result.conversations
    assert "automated instruction" not in result.conversations
    all_agents = await recall_for_turn(ctx, scope="all", cache=MemoryRecallCache())
    assert "other-agent" in all_agents.conversations
    assert "other-owner" not in all_agents.conversations


async def test_combined_evidence_budget_is_enforced(db, monkeypatch):
    await past(db, [("user", "Aster topic " + "explanation " * 100)] * 4)
    ctx = await context(db, "Tell me about Aster topic")
    monkeypatch.setattr(settings, "automatic_recall_max_chars", 500)
    async def unavailable(texts):
        raise ConnectionError()
    monkeypatch.setattr("app.services.embeddings.embed_texts", unavailable)
    result = await recall_for_turn(ctx, cache=MemoryRecallCache())
    assert len(result.facts) + len(result.conversations) <= 500


async def test_cross_language_query_preparation_is_shared_by_both_stores(db, monkeypatch):
    vector = np.array([1., 0., 0.], dtype=np.float32)
    await past(db, [("user", "My application deadline is Monday.")], vector=vector)
    ctx = await context(db, "Başvurunun son günü neydi?")
    calls = []
    async def expand(query):
        calls.append(("translation", query))
        return "application deadline", query + " application deadline"
    async def embed(texts):
        calls.append(("embedding", texts))
        return [vector]
    monkeypatch.setattr("app.services.query_translation.expand", expand)
    monkeypatch.setattr("app.services.embeddings.embed_texts", embed)
    cache = MemoryRecallCache()
    result = await recall_for_turn(ctx, cache=cache)
    await cache.close()
    assert "Monday" in result.conversations
    assert [kind for kind, _ in calls] == ["translation", "embedding"]


async def test_corrections_and_failed_action_receipts_remain_in_one_exchange(db, monkeypatch):
    await past(db, [("user", "Aster deadline is Friday."),
                    ("assistant", "I can submit it then."),
                    ("user", "Correction: Aster deadline is Monday; submit nothing yet.")])
    session = (await db.get(Session, 1))
    content = [{"type": "text", "text": "The later submission attempt failed."},
               {"type": "_speda_meta", "tools": [{"name": "submit_application", "result": "Error: submission rejected"}]}]
    await SessionManager().save_message(db, session.id, "assistant", content)
    ctx = await context(db, "Aster deadline and submission outcome?")
    async def offline(texts):
        raise ConnectionError()
    monkeypatch.setattr("app.services.embeddings.embed_texts", offline)
    cache = MemoryRecallCache()
    result = await recall_for_turn(ctx, cache=cache)
    await cache.close()
    assert "Correction" in result.conversations and "Monday" in result.conversations
    assert "submission rejected" in result.conversations
    assert "assistant message:" in result.conversations


async def test_irrelevant_vectors_are_not_presented_as_memory(db, monkeypatch):
    await past(db, [("user", "Pasta sauce simmered slowly.")], vector=np.array([1., 0.], dtype=np.float32))
    ctx = await context(db, "Tell me the robotics conference deadline")
    async def embed(texts):
        return [np.array([0., 1.], dtype=np.float32)]
    monkeypatch.setattr("app.services.embeddings.embed_texts", embed)
    cache = MemoryRecallCache()
    result = await recall_for_turn(ctx, cache=cache)
    await cache.close()
    assert not result.conversations and not result.facts


async def test_dimension_mismatch_is_observable_even_when_local_recall_survives(db, monkeypatch):
    await past(db, [("user", "Aster deadline is Monday.")], vector=np.array([1., 0.], dtype=np.float32))
    ctx = await context(db, "Aster deadline?")
    async def embed(texts):
        return [np.array([1., 0., 0.], dtype=np.float32)]
    monkeypatch.setattr("app.services.embeddings.embed_texts", embed)
    cache = MemoryRecallCache()
    result = await recall_for_turn(ctx, cache=cache)
    await cache.close()
    assert "Monday" in result.conversations
    assert "incompatible_vector_dimensions" in ctx.extra["recall_errors"]
    assert result.degraded


async def test_empty_cache_picks_up_first_background_embeddings_without_restart(db, monkeypatch):
    vector = np.array([1., 0.], dtype=np.float32)
    messages = await past(db, [("user", "I prefer explanations that get directly to the point.")])
    ctx = await context(db, "Use the concise version")
    async def embed(texts):
        return [vector]
    monkeypatch.setattr("app.services.embeddings.embed_texts", embed)
    cache = MemoryRecallCache()
    empty = await recall_for_turn(ctx, cache=cache)
    assert not empty.conversations
    db.add(MessageEmbedding(message_id=messages[0].id, session_id=messages[0].session_id,
        user_id=1, agent_id="atomix", role="user", text="I prefer explanations that get directly to the point.",
        embedding=vector.tobytes()))
    await db.commit()
    result = await recall_for_turn(ctx, cache=cache)
    await cache.close()
    assert "directly to the point" in result.conversations and not result.degraded


async def test_lexical_backfill_indexes_raw_messages_with_no_embedding(db, monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from sqlalchemy import text
    from app.services import embedding_indexer, lexical
    messages = await past(db, [("user", "Aster deadline is Monday.")])
    await db.execute(text("DELETE FROM messages_fts"))
    await db.commit()
    monkeypatch.setattr(embedding_indexer, "AsyncSessionLocal", async_sessionmaker(db.bind, expire_on_commit=False))
    assert await embedding_indexer.backfill_lexical(1, "backfill-test") == 1
    assert messages[0].id in await lexical.search(db, query="Aster", index=lexical.MESSAGES)


async def test_out_of_scope_keyword_hits_cannot_hide_an_in_scope_result(db):
    from app.services import lexical
    own = await past(db, [("user", "Aster has a longer application deadline explanation.")])
    for mid in range(1000, 1100):
        await lexical.index_message(db, mid, "Aster")
    await db.commit()
    assert await lexical.search(db, query="Aster", limit=1,
        allowed_ids={own[0].id}, index=lexical.MESSAGES) == [own[0].id]


async def test_local_fallback_survives_provider_deadline_with_realistic_raw_history(db, monkeypatch):
    from sqlalchemy import insert, text, select
    from app.models.message import Message
    from app.services import lexical
    messages = await past(db, [("user", "The Aster portfolio deadline is Monday.")])
    await db.execute(insert(Message), [{"session_id": messages[0].session_id,
        "role": "user", "created_at": datetime.now(timezone.utc),
        "content": [{"type": "text", "text": f"Unrelated background exchange {i}."}]}
        for i in range(20_635)])
    noise = (await db.execute(select(Message.id).where(Message.id != messages[0].id))).scalars().all()
    await db.execute(text("INSERT INTO messages_fts(rowid, text) VALUES (:id, :body)"),
        [{"id": mid, "body": lexical.fold(f"Fixture warehouse inventory slot {i % 97}, handoff batch {i // 97}.")}
         for i, mid in enumerate(noise)])
    await db.commit()
    ctx = await context(db, "When is the Aster portfolio due?")
    async def slow(texts):
        await asyncio.sleep(5)
    monkeypatch.setattr("app.services.embeddings.embed_texts", slow)
    cache = MemoryRecallCache()
    started = time.monotonic()
    result = await recall_for_turn(ctx, cache=cache)
    await cache.close()
    assert time.monotonic() - started < 1.8
    assert result.degraded and "Monday" in result.conversations
    assert f"message:{messages[0].id}" in result.conversations


async def test_scoped_sql_keyword_search_filters_before_limit(db):
    from sqlalchemy import select
    from app.models.message import Message
    from app.services import lexical
    own = await past(db, [("user", "Aster has a longer application deadline explanation.")])
    for mid in range(1000, 1100):
        await lexical.index_message(db, mid, "Aster")
    await db.commit()
    assert await lexical.search(db, query="Aster", limit=1, index=lexical.MESSAGES,
        allowed_ids_query=select(Message.id).where(Message.session_id == own[0].session_id)) == [own[0].id]


async def test_search_deduplicates_visible_reply_but_keeps_action_receipt(db):
    reply = "The document was prepared and delivery was attempted."
    messages = await past(db, [("user", "Please send the Aster application document."), ("assistant", reply)])
    messages[1].content = [*messages[1].content, {"type": "_speda_meta", "tools": [{"name": "send_file", "result": "Error: delivery rejected"}]}]
    await db.commit()
    ctx = await context(db, "Aster delivery result?")
    text = await search_conversations(ctx, {"query": "Aster", "_lexical_only": True, "_visible_texts": {reply}})
    assert reply not in text and "delivery rejected" in text


async def test_embedding_outage_keeps_durable_work_pending(db, monkeypatch):
    from app.services import embedding_indexer, task_queue
    from app.models.background_job import BackgroundJob
    from app.models.user import User
    from sqlalchemy import select
    db.add(User(id=1, name="Fixture", timezone="Europe/Istanbul"))
    await db.commit()
    messages = await past(db, [("user", "Aster deadline is Monday.")])
    factory = async_sessionmaker(db.bind, expire_on_commit=False)
    monkeypatch.setattr(embedding_indexer, "AsyncSessionLocal", factory)
    monkeypatch.setattr(task_queue, "AsyncSessionLocal", factory)
    async def offline(texts):
        raise ConnectionError("fixture outage")
    monkeypatch.setattr(embedding_indexer, "embed_texts", offline)
    job = BackgroundJob(user_id=1, session_id=messages[0].session_id, kind="embed_tail", status="running", attempts=1)
    db.add(job)
    await db.commit()
    assert not await task_queue._run_one(job.id, "embed_tail", job.session_id, 1, "test", "retry-test", {})
    await db.refresh(job)
    assert job.status == "pending" and "ConnectionError" in job.last_error
    assert not (await db.execute(select(MessageEmbedding))).scalars().all()


async def test_failed_local_index_retains_exact_retry_even_after_message_leaves_tail(db, monkeypatch):
    from app.services import embedding_indexer, task_queue, lexical
    from app.models.background_job import BackgroundJob
    from app.models.user import User
    from sqlalchemy import select
    db.add(User(id=1, name="Fixture", timezone="Europe/Istanbul"))
    await db.commit()
    original = lexical.index_message
    async def failed(reader, mid, text):
        return False
    monkeypatch.setattr(lexical, "index_message", failed)
    messages = await past(db, [("user", "Aster deadline is Monday.")])
    monkeypatch.setattr(lexical, "index_message", original)
    sessions = SessionManager()
    for _ in range(70):
        await sessions.save_message(db, messages[0].session_id, "user", "A later unrelated exchange.")
    factory = async_sessionmaker(db.bind, expire_on_commit=False)
    monkeypatch.setattr(embedding_indexer, "AsyncSessionLocal", factory)
    monkeypatch.setattr(task_queue, "AsyncSessionLocal", factory)
    job = (await db.execute(select(BackgroundJob).where(BackgroundJob.kind == "index_message"))).scalar_one()
    assert job.payload["message_id"] == messages[0].id
    assert await task_queue._run_one(job.id, "index_message", job.session_id, 1, "", "retry-test", job.payload)
    assert messages[0].id in await lexical.search(db, query="Aster", index=lexical.MESSAGES)
    await db.refresh(job)
    assert job.status == "done"


async def test_cold_matrix_finishes_after_deadline_and_next_recall_uses_warm_vectors(concurrent_db, monkeypatch):
    db = concurrent_db
    from app.skills import semantic_search
    vector = np.array([1., 0., 0.], dtype=np.float32)
    await past(db, [("user", "I prefer explanations that get directly to the point.")], vector=vector)
    ctx = await context(db, "Give me the concise explanation")
    cache = MemoryRecallCache()
    original = semantic_search._vectors_for
    async def slow_cold(reader, user_id, entries=None):
        if not entries:
            await asyncio.sleep(.12)
        return await original(reader, user_id, entries)
    async def embed(texts):
        return [vector]
    monkeypatch.setattr(semantic_search, "_vectors_for", slow_cold)
    monkeypatch.setattr("app.services.embeddings.embed_texts", embed)
    monkeypatch.setattr(settings, "automatic_recall_timeout_ms", 60)
    cold = await recall_for_turn(ctx, cache=cache)
    assert cold.degraded and ctx.extra["automatic_recall"]["deadline_missed"]
    await asyncio.gather(*cache.conversation_warmups.values())
    warm = await recall_for_turn(ctx, cache=cache)
    await cache.close()
    assert "directly to the point" in warm.conversations
    assert not warm.degraded
