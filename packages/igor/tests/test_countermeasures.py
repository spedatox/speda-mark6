# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.database import Base
from app.models.observation import Observation
from app.models.user import User
from app.services.countermeasures import create_countermeasure, record_outcome, start_run


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as session:
        session.add(User(id=1, name="owner", timezone="UTC"))
        pattern = Observation(
            user_id=1, observer="ultron", content="Professor X uses negative stems.",
            level="inductive", subject="person:Professor X", domain="project",
            source_ids=[1, 2], sources=["a", "b"], pattern_type="tendency",
            confidence="low",
        )
        session.add(pattern)
        await session.commit()
        yield session, pattern
    await engine.dispose()


async def test_unknown_is_not_silently_counted_as_success_and_completion_is_idempotent(db):
    session, pattern = db
    countermeasure = await create_countermeasure(
        session, user_id=1, namespace="academic.exam_strategy", title="Polarity",
        strategy="Restate negative qualifiers.", pattern_ids=[pattern.id],
        autonomy_level="adapt",
    )
    run = await start_run(
        session, countermeasure_id=countermeasure.id, user_id=1, agent_id="ultron",
        session_id=None, request_id="r1", trigger_patterns=[pattern.id],
    )
    await record_outcome(
        session, run_id=run.id, user_id=1, outcome="unknown",
        outcome_source="subsequent_behavior",
    )
    assert countermeasure.attempt_count == 1
    assert countermeasure.positive_count == 0
    assert countermeasure.effectiveness_score == 0.5

    # A retried queue job cannot count the same outcome twice.
    await record_outcome(
        session, run_id=run.id, user_id=1, outcome="success",
        outcome_source="owner_feedback",
    )
    assert countermeasure.attempt_count == 1
    assert countermeasure.positive_count == 0


async def test_known_outcomes_update_effectiveness_with_a_prior(db):
    session, pattern = db
    countermeasure = await create_countermeasure(
        session, user_id=1, namespace="academic.exam_strategy", title="Polarity",
        strategy="Restate negative qualifiers.", pattern_ids=[pattern.id],
    )
    run = await start_run(
        session, countermeasure_id=countermeasure.id, user_id=1, agent_id="ultron",
        session_id=None, request_id="r2", trigger_patterns=[pattern.id],
    )
    await record_outcome(
        session, run_id=run.id, user_id=1, outcome="success",
        outcome_source="owner_feedback",
    )
    assert countermeasure.effectiveness_score == 2 / 3
