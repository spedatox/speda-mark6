import json

import pytest

from app.schemas.iacon import Command
from app.schemas.llm import ProviderError
from app.skills.course_memory import ReadCourseMemorySkill


async def save_course(memory, catalogue, term, text, *, code="MIS231", owner=1):
    source, _ = await memory.seed(text, owner=owner, publish=False)
    path = f"/memories/academic/courses/{term}/{code}.md"
    return await catalogue.apply(owner, Command(
        command_id=path, title=path, kind="document", payload={"content": text},
        evidence=[{"source_id": source, "start": 0, "end": len(text), "quote": text}],
    ), actor="migration")


async def test_startup_exposes_saved_course_reader_to_ultron(memory, monkeypatch):
    from app import database
    from app.main import create_app

    monkeypatch.setattr(database, "engine", memory.engine)
    monkeypatch.setattr(database, "AsyncSessionLocal", memory.sessions)
    app = create_app()
    async with app.router.lifespan_context(app):
        state = app.state
        text = "# MIS231 — Course record\n\n## Lecture Log\n\nSaved lecture notes."
        saved = await save_course(memory, state.iacon, "2026-2027-fall", text)
        ultron = state.profiles.require("ultron")
        context = memory.context("Did I fill you in about the data structures class?",
                                 agent_id="ultron", allowed_tools=ultron.tools)
        assert "read_course_memory" in ultron.character
        assert "read_course_memory" in {tool["name"] for tool in state.registry.list_tools(context)}
        result = json.loads(await state.registry.execute("read_course_memory", {"course_code": "mis 231"}, context))
        assert result["status"] == "found"
        assert result["record_id"] == saved["id"]
        assert result["text"] == text
        with pytest.raises(ProviderError, match="not authorized"):
            await state.registry.execute("read_course_memory", {"course_code": "MIS231"}, memory.context())


async def test_course_reads_disambiguate_terms_page_content_and_respect_evidence(memory):
    from app.services.iacon_catalogue import Catalogue

    catalogue = Catalogue(memory.store)
    skill = ReadCourseMemorySkill(catalogue)
    text = "Saved lecture notes. " * 200
    older = await save_course(memory, catalogue, "2025-2026-fall", "Earlier class.")
    newer = await save_course(memory, catalogue, "2026-2027-fall", text)
    await save_course(memory, catalogue, "2027-2028-fall", "Another owner's class.", owner=2)
    another = await save_course(memory, catalogue, "2026-2027-fall", "Statistics notes.", code="MIS215")

    async with memory.sessions() as db:
        context = memory.context(agent_id="ultron")
        context.db = db
        listing = json.loads(await skill.execute({"course_code": "MIS231"}, context))
        assert listing["status"] == "select_term"
        assert [record["record_id"] for record in listing["records"]] == [newer["id"], older["id"]]
        listing = json.loads(await skill.execute({"term": "2026-2027-fall"}, context))
        assert {record["record_id"] for record in listing["records"]} == {newer["id"], another["id"]}

        args = {"course_code": "MIS231", "term": "2026-2027-fall"}
        first = json.loads(await skill.execute(args, context))
        second = json.loads(await skill.execute({**args, "offset": first["next_offset"]}, context))
        assert first["text"] + second["text"] == text
        assert second["next_offset"] is None

    await memory.store.revoke(memory.context(), newer["evidence"][0]["source_id"])
    async with memory.sessions() as db:
        context.db = db
        assert json.loads(await skill.execute(args, context)) == {"status": "withdrawn"}


async def test_course_name_uses_timetable_and_archive_heading(memory):
    from app.models.academic import CourseSlot
    from app.services.iacon_catalogue import Catalogue

    catalogue = Catalogue(memory.store)
    skill = ReadCourseMemorySkill(catalogue)
    term = "2026-2027-fall"
    text = "# MIS243 — Financial Accounting\n\n## Overview\n\nCourse name is General Accounting."
    saved = await save_course(memory, catalogue, term, text, code="MIS243")
    older = await save_course(memory, catalogue, "2025-2026-fall", text, code="MIS243")
    await save_course(memory, catalogue, term, "# MIS215 — General Accounting", code="MIS215", owner=2)
    await save_course(memory, catalogue, term, "# MIS251 — Marketing\n\nGeneral Accounting notes.", code="MIS251")

    async with memory.sessions() as db:
        db.add_all([
            CourseSlot(slot_id=f"mis243-{hour}", code="MIS 243", name="General Accounting",
                       day_of_week="MONDAY", start_time=f"{hour}:00", end_time=f"{hour}:50")
            for hour in (10, 11)
        ])
        await db.commit()
        context = memory.context(agent_id="ultron")
        context.db = db
        args = {"course_name": "  GENERAL   ACCOUNTING  "}
        listing = json.loads(await skill.execute(args, context))
        assert listing["status"] == "select_term"
        assert [record["record_id"] for record in listing["records"]] == [saved["id"], older["id"]]
        result = json.loads(await skill.execute({**args, "term": term}, context))
        assert result["status"] == "found"
        assert result["record_id"] == saved["id"]
        assert result["text"] == text
        result = json.loads(await skill.execute({"course_name": "Financial Accounting", "term": term}, context))
        assert result["record_id"] == saved["id"]
        assert json.loads(await skill.execute({"course_name": "Accounting"}, context))["status"] == "no_matches"
