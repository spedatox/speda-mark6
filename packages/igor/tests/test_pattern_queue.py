# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

from unittest.mock import patch

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.database import Base
from app.models.background_job import BackgroundJob
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
