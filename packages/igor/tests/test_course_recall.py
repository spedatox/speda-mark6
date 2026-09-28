"""Course records are discoverable without flooding every agent's prompt."""

from types import SimpleNamespace
from datetime import date
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models
from app.core.registry import CapabilityRegistry
from app.database import Base
from app.models.memory_file import MemoryFile
from app.models.memory_write_receipt import MemoryWriteReceipt
from app.models.message import Message
from app.models.session import Session
from app.profiles.ultron import UltronProfile
from app.skills.course_memory import CourseMemorySkill, ReadCourseMemorySkill
from app.skills.memory import (
    MemoryRecallCache, MemorySkill, document_query_for_history,
    recall_for_context, relevant_files_for_message,
)


@pytest.fixture
async def sessions():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_ultron_has_course_tools_without_search():
    registry = CapabilityRegistry()
    await registry.register_skill(ReadCourseMemorySkill())
    await registry.register_skill(CourseMemorySkill())
    ultron = {tool["name"] for tool in registry.list_tools(agent_id="ultron")}
    speda = {tool["name"] for tool in registry.list_tools(agent_id="speda")}
    assert {"read_course_memory", "record_course_memory"} <= ultron
    assert not {"read_course_memory", "record_course_memory"} & speda
    assert "read_course_memory" in UltronProfile().build_system_prompt({})
    assert MemorySkill.input_schema["properties"]["command"]["enum"] == ["view"]
    assert "Raw memory writes are disabled" in await MemorySkill().execute(
        {"command": "create", "path": "/memories/academic/courses/2026-2027-spring/ATA101.md"},
        SimpleNamespace(),
    )


async def test_read_course_memory_lists_ambiguous_terms_then_opens_exact_record(sessions):
    async with sessions() as db:
        db.add_all([
            MemoryFile(user_id=1, path="/memories/academic/courses/2025-2026-spring/ATA101.md",
                       content="# ATA101 — Old term\n\n## Overview\nOld syllabus."),
            MemoryFile(user_id=1, path="/memories/academic/courses/2026-2027-spring/ATA101.md",
                       content="# ATA101 — New term\n\n## Assessments\nMidterm on 2027-03-12."),
            MemoryFile(user_id=2, path="/memories/academic/courses/2026-2027-spring/YBS102.md",
                       content="# Private course"),
        ])
        await db.commit()
        ctx = SimpleNamespace(db=db, user_id=1)
        skill = ReadCourseMemorySkill()
        choices = await skill.execute({"course_code": "ATA101"}, ctx)
        assert "2025-2026-spring" in choices and "2026-2027-spring" in choices
        assert "Old syllabus" not in choices and "Midterm" not in choices
        exact = await skill.execute({"course_code": "ata101", "term": "2026-2027-spring"}, ctx)
        assert "Midterm on 2027-03-12" in exact
        assert "Old syllabus" not in exact and "Private course" not in exact


async def test_standing_context_is_small_and_course_recall_is_selective(sessions):
    async with sessions() as db:
        db.add_all([
            MemoryFile(user_id=1, path="/memories/academic/courses/2026-2027-spring/ATA101.md",
                       content="# ATA101 — Atatürk İlkeleri\n\n## Assessments\nMidterm on 2027-03-12."),
            MemoryFile(user_id=1, path="/memories/academic/courses/2026-2027-spring/YBS102.md",
                       content="# YBS102 — Databases\n\n## Materials\nSQL slides."),
            MemoryFile(user_id=2, path="/memories/academic/courses/2026-2027-spring/ATA101.md",
                       content="# ATA101\n\nPrivate owner's grade: 10."),
        ])
        await db.commit()
        stable = await recall_for_context(1, db, "ultron", cache=MemoryRecallCache())
        assert "Midterm on 2027-03-12" not in stable
        assert "SQL slides" not in stable
        selected = await relevant_files_for_message(1, db, "ATA101 sınavı ne zaman?")
        assert "Midterm on 2027-03-12" in selected
        assert "SQL slides" not in selected and "Private owner's grade" not in selected
        assert len(selected) <= 6000
        ambiguous = await relevant_files_for_message(1, db, "YBS102 ders notları")
        assert "SQL slides" in ambiguous and "Midterm" not in ambiguous
        biography = await relevant_files_for_message(1, db, "geçmişim hakkında anlat")
        assert "### /memories/owner.md" in biography
        assert await relevant_files_for_message(1, db, "tamam") == ""


async def test_course_write_is_idempotent_and_uses_owner_date(sessions, monkeypatch):
    reviewer = AsyncMock(return_value="Supported course note")
    monkeypatch.setattr("app.services.memory_admission.admit", reviewer)
    monkeypatch.setattr("app.skills.course_memory.owner_today", lambda: date(2027, 2, 16))
    async with sessions() as db:
        session = Session(user_id=1, agent_id="ultron", triggered_by="user", model_used="test")
        db.add(session)
        await db.flush()
        db.add(Message(session_id=session.id, role="user", content="ATA101 dersinde ilk reform dönemi işlendi."))
        await db.commit()
        context = SimpleNamespace(db=db, user_id=1, session_id=session.id,
                                  agent_id="ultron", request_id="course-1", model="test")
        args = {"term": "2026-2027-spring", "course_code": "ATA101",
                "section": "Lecture Log", "entry": "İlk reform dönemi işlendi.",
                "evidence": [{"ref": "message:latest", "quote": "ilk reform dönemi işlendi"}]}
        skill = CourseMemorySkill()
        assert (await skill.execute(args, context)).startswith("Recorded in")
        assert (await skill.execute(args, context)).startswith("Already recorded in")
        record = (await db.execute(select(MemoryFile).where(
            MemoryFile.path == "/memories/academic/courses/2026-2027-spring/ATA101.md",
        ))).scalar_one()
        assert "### 2027-02-16" in record.content
        assert record.content.count("- İlk reform dönemi işlendi.") == 1
        assert len((await db.execute(select(MemoryWriteReceipt))).scalars().all()) == 1
        assert reviewer.await_count == 1


async def test_course_recall_disambiguates_terms_and_bounds_long_documents(sessions):
    async with sessions() as db:
        db.add_all([
            MemoryFile(user_id=1, path="/memories/academic/courses/2025-2026-spring/ATA101.md",
                       content="# ATA101\nOld course."),
            MemoryFile(user_id=1, path="/memories/academic/courses/2026-2027-spring/ATA101.md",
                       content="# ATA101\nNew course.\n" + "Lecture material. " * 2000),
        ])
        await db.commit()
        choices = await relevant_files_for_message(1, db, "ATA101 sınavı")
        assert "Course record choices" in choices and "New course." not in choices
        selected = await relevant_files_for_message(1, db, "ATA101 2026-2027-spring sınavı")
        assert "New course." in selected and "Old course." not in selected
        assert len(selected) <= 6000


def test_short_course_follow_up_keeps_only_the_prior_course_identifier():
    history = [
        {"role": "user", "content": "ATA101 2026-2027-spring dersinin notlarını aç"},
        {"role": "assistant", "content": "Bu derste konuştuğumuz başka ayrıntılar..."},
        {"role": "user", "content": "Peki sınavı ne zaman?"},
    ]
    query = document_query_for_history(history)
    assert "ATA101" in query and "2026-2027-spring" in query
    assert "notlarını aç" not in query
    history[-1]["content"] = "Başka dersin sınavını soruyorum"
    assert "ATA101" not in document_query_for_history(history)
