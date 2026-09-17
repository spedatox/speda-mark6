# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.database import Base
from app.models.observation import Observation
from app.models.pattern import PatternEvidence, PatternState
from app.models.user import User
from app.services.pattern_evidence import sync_inductive_observation


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


async def test_inductive_observation_becomes_machine_addressable_and_active(db):
    sources = [
        Observation(
            user_id=1, observer="ultron", origin="owner", content=f"Exam {i} used a negative stem.",
            level="explicit", subject="person:Professor X", domain="project",
        )
        for i in range(3)
    ]
    db.add_all(sources)
    await db.flush()
    pattern = Observation(
        user_id=1,
        observer="ultron",
        content="Professor X repeatedly uses negative question stems.",
        level="inductive",
        subject="person:Professor X",
        domain="project",
        source_ids=[source.id for source in sources],
        sources=[source.content for source in sources],
        pattern_type="tendency",
        confidence="low",
    )
    db.add(pattern)
    await db.commit()

    state = await sync_inductive_observation(db, pattern)

    assert state.status == "active"
    assert state.support_count == 3
    assert state.independent_source_count == 3
    assert pattern.confidence in {"medium", "high"}
    assert await db.get(PatternState, pattern.id) is not None


async def test_synthetic_evidence_is_retained_but_has_zero_scoring_weight(db):
    source = Observation(
        user_id=1, observer="ultron", origin="synthetic",
        content="A generated practice item used a negative stem.",
        level="explicit", subject="person:Professor X", domain="project",
    )
    second = Observation(
        user_id=1, observer="ultron", origin="owner",
        content="The owner reported one real negative stem.",
        level="explicit", subject="person:Professor X", domain="project",
    )
    db.add_all([source, second])
    await db.flush()
    pattern = Observation(
        user_id=1, observer="ultron", content="Professor X uses negative stems.",
        level="inductive", subject="person:Professor X", domain="project",
        source_ids=[source.id, second.id], sources=[source.content, second.content],
        pattern_type="tendency", confidence="low",
    )
    db.add(pattern)
    await db.commit()

    state = await sync_inductive_observation(db, pattern)
    evidence = list((await db.execute(
        __import__("sqlalchemy").select(PatternEvidence).where(
            PatternEvidence.pattern_observation_id == pattern.id
        )
    )).scalars().all())

    assert len(evidence) == 2
    assert next(row for row in evidence if row.trust_class == "synthetic").weight == 0
    assert state.support_count == 1
    assert state.status == "candidate"
