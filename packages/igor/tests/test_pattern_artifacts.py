# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

import base64
from unittest.mock import patch

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.database import Base
from app.models.pattern import Countermeasure, PatternEvidence, PatternState
from app.models.project import Project, ProjectFile
from app.models.user import User
from app.services.attachments import extract_document
from app.services.pattern_analysis import _artifact_features, analyze_artifact


@pytest_asyncio.fixture
async def sessions():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as db:
        db.add(User(id=1, name="owner", timezone="UTC"))
        project = Project(user_id=1, agent_id="ultron", name="Database Exam")
        db.add(project)
        await db.flush()
        for number in range(1, 4):
            db.add(ProjectFile(
                project_id=project.id,
                name=f"exam-{number}.txt",
                content="1. Which statement is NOT correct?\nA) One\nB) Two",
                chars=54,
                content_hash=str(number) * 64,
            ))
        await db.commit()
    yield maker
    await engine.dispose()


def test_structured_extraction_preserves_question_locators_and_hash():
    body = """# Exam
1. Which statement is NOT correct?
A) One
B) Two

2. Pick the ordinary answer.
A) Three
B) Four
"""
    document = extract_document(
        "exam.txt", "text/plain", base64.b64encode(body.encode()).decode()
    )
    assert len(document.content_hash) == 64
    assert [segment.item_number for segment in document.segments] == ["1", "2"]
    assert document.segments[0].char_start < document.segments[0].char_end


def test_academic_features_count_a_file_as_feature_evidence():
    content = """1. Which option is NOT valid?
A) A
B) B
2. Which option is correct?
A) A
B) B
"""
    assert _artifact_features(content) == {
        "negative_stem": 1,
        "multiple_choice": 2,
    }


async def test_three_artifacts_activate_pattern_with_independent_provenance(sessions):
    with patch("app.services.pattern_analysis.AsyncSessionLocal", sessions):
        for file_id in (1, 2, 3):
            await analyze_artifact(user_id=1, project_file_id=file_id)

    async with sessions() as db:
        states = list((await db.execute(select(PatternState))).scalars().all())
        evidence = list((await db.execute(select(PatternEvidence))).scalars().all())
        countermeasures = list((await db.execute(select(Countermeasure))).scalars().all())

    negative = next(state for state in states if state.feature_json.get("mechanic") == "negative_stem")
    assert negative.status == "active"
    assert negative.independent_source_count == 3
    negative_evidence = [row for row in evidence if row.pattern_observation_id == negative.observation_id]
    assert {row.source_group for row in negative_evidence} == {
        "project_file:1", "project_file:2", "project_file:3",
    }
    assert all(row.evidence_kind == "project_file" and row.source_hash for row in negative_evidence)
    assert any(cm.title == "Negation stem check" and cm.autonomy_level == "adapt"
               for cm in countermeasures)
