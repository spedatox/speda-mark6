# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

from types import SimpleNamespace
from unittest.mock import AsyncMock, create_autospec, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.database import Base
from app.models.background_job import BackgroundJob
from app.models.message import Message
from app.models.session import Session
from app.models.user import User
from app.services import task_queue


@pytest_asyncio.fixture
async def sessions():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as db:
        db.add(User(id=1, name="owner", timezone="UTC"))
        await db.commit()
    yield maker
    await engine.dispose()


async def test_payload_jobs_deduplicate_exact_source_but_not_siblings(sessions):
    with patch.object(task_queue, "AsyncSessionLocal", sessions):
        first = await task_queue.enqueue_payload_job(
            kind="analyze_artifact", unique_key="project_file:81",
            payload={"project_file_id": 81}, user_id=1,
        )
        duplicate = await task_queue.enqueue_payload_job(
            kind="analyze_artifact", unique_key="project_file:81",
            payload={"project_file_id": 81}, user_id=1,
        )
        second = await task_queue.enqueue_payload_job(
            kind="analyze_artifact", unique_key="project_file:82",
            payload={"project_file_id": 82}, user_id=1,
        )
    assert first is not None and duplicate is None and second is not None
    async with sessions() as db:
        jobs = list((await db.execute(select(BackgroundJob))).scalars().all())
    assert {job.unique_key for job in jobs} == {"project_file:81", "project_file:82"}


@pytest.mark.parametrize("kind,service", [
    ("session_log", "update_session_log"),
    ("session_recap", "update_session_recap"),
    ("daily_maintenance", "run_daily_maintenance"),
])
async def test_memory_jobs_reach_their_real_handler_signature(sessions, kind, service):
    from app.services import memory

    handler = create_autospec(getattr(memory, service))
    with patch.object(memory, service, handler), patch.object(task_queue, "AsyncSessionLocal", sessions):
        job_id = await task_queue.enqueue_one(
            kind=kind, user_id=1, model="test", request_id="continuity-test",
        )
        result = await task_queue.drain()
    assert result == {"claimed": 1, "succeeded": 1, "failed": 0}
    handler.assert_awaited_once_with(None, "continuity-test", 1, "test")
    async with sessions() as db:
        assert (await db.get(BackgroundJob, job_id)).status == "done"


async def _queue_recap(sessions):
    async with sessions() as db:
        session = Session(user_id=1, agent_id="atomix", triggered_by="user", model_used="test")
        db.add(session)
        await db.flush()
        messages = [
            Message(session_id=session.id, role="user", content="My exercise plan is four days a week."),
            Message(session_id=session.id, role="assistant", content="We will split those across the week."),
        ]
        db.add_all(messages)
        await db.flush()
        job = BackgroundJob(
            user_id=1, session_id=session.id, kind="session_recap",
            payload={"model": "test"}, request_id="continuity-test",
        )
        db.add(job)
        await db.commit()
        return session.id, job.id, messages[-1].id


async def test_queued_recap_reaches_the_next_conversation(sessions, monkeypatch):
    from app.config import settings
    from app.services import memory, llm_client
    from app.skills.memory import MemoryRecallCache, recall_sessions_for_context

    monkeypatch.setattr(settings, "episodic_recap_enabled", True)
    old_id, job_id, last_message_id = await _queue_recap(sessions)
    recap = "The owner chose a four-day exercise plan. The weekly split is still open."
    response = SimpleNamespace(content=[SimpleNamespace(type="text", text=recap)], stop_reason="end_turn")
    create_message = AsyncMock(return_value=response)
    with patch.object(task_queue, "AsyncSessionLocal", sessions), patch.object(memory, "AsyncSessionLocal", sessions), \
            patch.object(llm_client.LLMClient, "create_message", create_message):
        assert await task_queue.drain() == {"claimed": 1, "succeeded": 1, "failed": 0}

    async with sessions() as db:
        old = await db.get(Session, old_id)
        assert old.recap == recap and old.recap_through_id == last_message_id
        assert (await db.get(BackgroundJob, job_id)).status == "done"
        current = Session(user_id=1, agent_id="atomix", triggered_by="user", model_used="test")
        db.add(current)
        await db.commit()
        block = await recall_sessions_for_context(1, db, "atomix", current.id, cache=MemoryRecallCache())
        assert recap in block
        assert await recall_sessions_for_context(1, db, "atomix", old.id, cache=MemoryRecallCache()) == ""
    assert create_message.await_count == 1
    assert create_message.call_args.kwargs["model"] == "test"


@pytest.mark.parametrize("failure", ["provider", "empty", "truncated"])
async def test_failed_recap_stays_retryable_without_advancing_history(sessions, monkeypatch, failure):
    from app.config import settings
    from app.services import memory, llm_client

    monkeypatch.setattr(settings, "episodic_recap_enabled", True)
    session_id, job_id, _ = await _queue_recap(sessions)
    if failure == "provider":
        create_message = AsyncMock(side_effect=RuntimeError("provider unavailable"))
    else:
        create_message = AsyncMock(return_value=SimpleNamespace(
            content=[] if failure == "empty" else [SimpleNamespace(type="text", text="Partial recap")],
            stop_reason="end_turn" if failure == "empty" else "max_tokens",
        ))
    with patch.object(task_queue, "AsyncSessionLocal", sessions), patch.object(memory, "AsyncSessionLocal", sessions), \
            patch.object(llm_client.LLMClient, "create_message", create_message):
        assert await task_queue.drain() == {"claimed": 1, "succeeded": 0, "failed": 1}
    assert create_message.await_count == 1
    async with sessions() as db:
        job = await db.get(BackgroundJob, job_id)
        session = await db.get(Session, session_id)
        assert job.status == "pending" and job.attempts == 1 and job.last_error
        assert session.recap is None and session.recap_through_id is None
