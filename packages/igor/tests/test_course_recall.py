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
from app.models.tool_call import ToolCall
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
    assert MemorySkill.input_schema["properties"]["command"]["enum"] == ["view", "search_sources"]
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
        assert len(stable) < 5000
        assert "Midterm on 2027-03-12" not in stable
        assert "SQL slides" not in stable
        selected = await relevant_files_for_message(1, db, "ATA101 sınavı ne zaman?")
        assert "Midterm on 2027-03-12" in selected
        assert "SQL slides" not in selected and "Private owner's grade" not in selected
        assert len(selected) <= 2800
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


@pytest.mark.parametrize("neutral_heading", ["# MIS231 — Course record", "# MIS231"])
async def test_timetable_evidence_creates_and_enriches_code_only_records(sessions, monkeypatch, neutral_heading):
    import json
    from app.skills.course_memory import _new_course
    reviewer = AsyncMock(return_value="Timetable supports the exact course and instructor.")
    monkeypatch.setattr("app.services.memory_admission.admit", reviewer)
    async with sessions() as db:
        session = Session(user_id=1, agent_id="ultron", triggered_by="user", model_used="test")
        db.add(session)
        await db.flush()
        courses = [{"code": "MIS231", "name": "Data Structures and Algorithms",
                    "instructor": "Dr. Örnek Öğretmen"},
                   {"code": "WAP225", "name": "Workplace Application I",
                    "instructor": "Dr. Placement Teacher"},
                   {"code": "MIS251", "name": "Marketing Principles and E-Commerce",
                    "instructor": "Dr. Marketing Teacher"},
                   {"code": "MAR411", "name": "Supply Chain Management and Logistics",
                    "instructor": "Dr. Logistics Teacher"}]
        call = ToolCall(session_id=session.id, request_id="schedule", tool_name="read_schedule",
                        tool_input={}, tool_result=json.dumps({"courses": courses}, ensure_ascii=False, separators=(",", ":")))
        path = "/memories/academic/courses/2026-2027-fall/MIS231.md"
        original = _new_course("MIS231", "").replace("# MIS231 — Course record", neutral_heading, 1) + "\nA unique old lecture note.\n"
        db.add_all([call, MemoryFile(user_id=1, path=path, content=original)])
        await db.commit()
        ctx = SimpleNamespace(db=db, user_id=1, session_id=session.id,
                              agent_id="ultron", request_id="course", model="test")
        for course in courses:
            args = {"term": "2026-2027-fall", "course_code": course["code"],
                    "course_name": course["name"], "section": "Overview",
                    "entry": f"Instructor: {course['instructor']}.",
                    "evidence": [{"ref": "tool_call:read_schedule", "quote": json.dumps(course, ensure_ascii=False, separators=(",", ":"))}]}
            assert (await CourseMemorySkill().execute(args, ctx)).startswith("Recorded in")
            assert (await CourseMemorySkill().execute(args, ctx)).startswith("Already recorded in")
        content = (await db.execute(select(MemoryFile.content).where(MemoryFile.path == path))).scalar_one()
        assert content.startswith("# MIS231 — Data Structures and Algorithms")
        assert "Dr. Örnek Öğretmen" in content and "unique old lecture note" in content
        assert "## Materials\n\n- _(none recorded)_" in content
        assert "## Assessments\n\n- _(none recorded)_" in content
        receipts = (await db.execute(select(MemoryWriteReceipt))).scalars().all()
        assert len(receipts) == 4
        assert all(r.evidence[0]["ref"] == f"tool_call:{call.id}" for r in receipts)
        assert all(r.evidence[0]["source_authority"] == "tool_result" for r in receipts)
        assert reviewer.await_count == 4


@pytest.mark.parametrize("tool_name", ["calendar_list_events", "calendar_get_event", "outlook_read_email"])
async def test_retrieved_course_details_need_no_owner_restatement(sessions, monkeypatch, tool_name):
    from app.skills.course_memory import _new_course
    async def review(system, payload, **kwargs):
        assert "The owner need not repeat or explicitly confirm" in system
        assert payload["evidence"][0]["source_authority"] == "tool_result"
        assert f"Tool `{tool_name}`" in payload["evidence"][0]["source_body"]
        assert "CAL101 — Biology I" in payload["evidence"][0]["quote"]
        return {"allow": True, "reason": "Retrieved source supports the course details."}
    reviewer = AsyncMock(side_effect=review)
    monkeypatch.setattr("app.services.memory_admission.ask_json", reviewer)
    async with sessions() as db:
        session = Session(user_id=1, agent_id="ultron", triggered_by="user", model_used="test")
        db.add(session)
        await db.flush()
        source = "CAL101 — Biology I; instructor Dr. Example; scheduled on 2026-10-16 at 09:00."
        call = ToolCall(session_id=session.id, request_id="read", tool_name=tool_name,
                        tool_input={}, tool_result=source)
        path = "/memories/academic/courses/2026-2027-fall/CAL101.md"
        before = _new_course("CAL101", "") + "\nKeep this original note.\n"
        db.add_all([call, MemoryFile(user_id=1, path=path, content=before)])
        await db.commit()
        ctx = SimpleNamespace(db=db, user_id=1, session_id=session.id,
                              agent_id="ultron", request_id="write", model="test")
        skill = CourseMemorySkill()
        # Even the name-only correction route can fill an unnamed record from
        # a retrieved source; no synthetic owner message is necessary.
        args = {"operation": "confirm_identity", "term": "2026-2027-fall",
                "course_code": "CAL101", "course_name": "Biology I",
                "evidence": [{"ref": f"tool_call:{tool_name}", "quote": source}]}
        assert (await skill.execute(args, ctx)).startswith("Recorded in")
        args.update(operation="append", section="Overview", entry="Instructor: Dr. Example.")
        assert (await skill.execute(args, ctx)).startswith("Recorded in")
        current = (await db.execute(select(MemoryFile.content).where(MemoryFile.path == path))).scalar_one()
        assert current.startswith("# CAL101 — Biology I")
        assert "Instructor: Dr. Example." in current and "Keep this original note." in current
        assert reviewer.await_count == 2
