# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.database import Base
from app.models.observation import Observation
from app.models.pattern import Countermeasure, CountermeasurePattern, PatternState
from app.models.user import User
from app.services.tactical_context import tactical_context_for_message


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as session:
        session.add(User(id=1, name="owner", timezone="UTC"))
        await session.commit()
        yield session
    await engine.dispose()


async def test_relevant_active_pattern_and_countermeasure_are_injected(db):
    observation = Observation(
        user_id=1, observer="ultron", content="Professor X uses negative exam stems.",
        level="inductive", subject="person:Professor X", domain="project",
        source_ids=[1, 2, 3], sources=["a", "b", "c"],
        pattern_type="tendency", confidence="high",
    )
    db.add(observation)
    await db.flush()
    db.add(PatternState(
        observation_id=observation.id, user_id=1, namespace="academic.exam_style",
        scope_type="person", scope_key="person:Professor X", status="active",
        confidence_score=0.86, support_count=3, independent_source_count=3,
        tags=["academic", "exam", "negative_stem"],
    ))
    countermeasure = Countermeasure(
        user_id=1, namespace="academic.exam_strategy", title="Polarity check",
        strategy="Restate negative qualifiers before solving.", status="active",
        autonomy_level="adapt", effectiveness_score=0.83,
    )
    db.add(countermeasure)
    await db.flush()
    db.add(CountermeasurePattern(
        countermeasure_id=countermeasure.id, pattern_observation_id=observation.id,
    ))
    await db.commit()

    block = await tactical_context_for_message(
        1, db, [{"role": "user", "content": "Help me study for Professor X's exam"}],
        agent_id="ultron",
    )

    assert "## Tactical Context" in block
    assert "Professor X uses negative exam stems" in block
    assert "Restate negative qualifiers" in block


async def test_unrelated_pattern_is_not_injected(db):
    observation = Observation(
        user_id=1, observer="sentinel", content="Grocery spending rises on Fridays.",
        level="inductive", subject="owner", domain="finance", source_ids=[1, 2, 3],
        sources=["a", "b", "c"], pattern_type="tendency", confidence="high",
    )
    db.add(observation)
    await db.flush()
    db.add(PatternState(
        observation_id=observation.id, user_id=1, namespace="finance.spending",
        scope_type="owner", scope_key="owner", status="active", confidence_score=0.9,
        support_count=3, independent_source_count=3,
    ))
    await db.commit()

    block = await tactical_context_for_message(
        1, db, [{"role": "user", "content": "Debug this Python deployment failure"}],
        agent_id="optimus",
    )
    assert block == ""
