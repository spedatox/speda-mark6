"""Owner-confirmed course/employment incident, source parity and safe resumption."""
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models
from app.database import Base
from app.models.memory_file import MemoryFile
from app.models.memory_source import MemorySource
from app.models.memory_revision import MemoryRevision
from app.models.user import User
from app.services.memory_owner_review import apply_owner_unit
from app.services.memory_states import encode, freshness, parse, render, verify_source, version
from app.services.memory_store import MemoryWriteConflict, mutate_file
from app.services.memory_schema import MemorySchemaViolation
from app.skills.course_memory import CourseMemorySkill, _new_course
from app.skills.memory import MemorySkill, bounded_excerpt
from app.skills.memory_state import MemoryStateSkill


@pytest.fixture
async def sessions():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def owner_source():
    return {"key": "owner-course-confirmation", "content": "ABC243 General Accounting dersi.",
            "classification": "owner_confirmation", "original_path": "external:test-owner-message",
            "original_row": {"kind": "owner_confirmation", "recorded_on": "2026-10-01"}}


async def test_owner_correction_preserves_original_and_resumes_without_provider(sessions, monkeypatch):
    reviewer = AsyncMock(side_effect=AssertionError("No provider allowed"))
    monkeypatch.setattr("app.services.memory_admission.admit", reviewer)
    path = "/memories/academic/courses/2026-2027-fall/ABC243.md"
    before = _new_course("ABC243", "Financial Accounting")
    after = before.replace("Financial Accounting", "General Accounting", 1)
    unit = {"path": path, "before_hash": version(before), "after": after,
            "evidence": [{"key": "owner-course-confirmation", "quote": "ABC243 General Accounting dersi."}]}
    async with sessions() as db:
        db.add_all([User(id=1, name="Owner", timezone="Europe/Istanbul"),
                    MemoryFile(user_id=1, path=path, content=before)])
        await db.commit()
        first = await apply_owner_unit(db, user_id=1, unit=unit, sources=[owner_source()], plan_id="test-plan")
        second = await apply_owner_unit(db, user_id=1, unit=unit, sources=[owner_source()], plan_id="test-plan")
        assert not first["resumed"] and second["resumed"]
        assert first["receipt_id"] == second["receipt_id"]
        originals = (await db.execute(select(MemorySource))).scalars().all()
        assert any(source.content == before for source in originals)
        assert len((await db.execute(select(MemoryRevision))).scalars().all()) == 1
        source = next(source for source in originals if source.classification == "owner_confirmation")
        await verify_source(db, 1, f"source:{source.id}")
        with pytest.raises(ValueError):
            await verify_source(db, 2, f"source:{source.id}")
        read = await MemorySkill().execute({"command": "view", "path": f"source:{source.id}"}, SimpleNamespace(db=db, user_id=1))
        assert "Owner confirmation" in read
    reviewer.assert_not_called()


async def test_stale_review_rolls_back_sources_and_does_not_replay_other_units(sessions):
    async with sessions() as db:
        db.add(MemoryFile(user_id=1, path="/memories/projects/demo.md", content="# Demo\nNew content"))
        await db.commit()
        with pytest.raises(MemoryWriteConflict):
            await apply_owner_unit(db, user_id=1, plan_id="p", sources=[owner_source()], unit={
                "path": "/memories/projects/demo.md", "before_hash": version("old content"),
                "after": "# Demo\nReplacement", "evidence": [{"key": "owner-course-confirmation", "quote": "Accounting"}]})
        assert not (await db.execute(select(MemorySource))).scalars().all()
        assert not (await db.execute(select(MemoryRevision))).scalars().all()


async def test_every_agent_write_path_rejects_course_rename_before_reviewer(sessions, monkeypatch):
    reviewer = AsyncMock(return_value="Approved")
    monkeypatch.setattr("app.services.memory_admission.admit", reviewer)
    path = "/memories/academic/courses/2026-2027-fall/ABC243.md"
    before = _new_course("ABC243", "General Accounting")
    async with sessions() as db:
        db.add(MemoryFile(user_id=1, path=path, content=before))
        await db.commit()
        for after in (before.replace("General Accounting", "Object Oriented Programming", 1),
                      before.replace("# ABC243", "# ABC244", 1)):
            with pytest.raises(MemorySchemaViolation):
                await mutate_file(db, user_id=1, path=path, before=before, after=after,
                    author="ultron", action="memory_edit", managed=True,
                    evidence=[{"ref": path, "quote": "General Accounting"}])
    reviewer.assert_not_called()


async def test_new_course_name_requires_literal_code_bound_evidence(sessions, monkeypatch):
    reviewer = AsyncMock(return_value="Approved")
    monkeypatch.setattr("app.services.memory_admission.admit", reviewer)
    async with sessions() as db:
        evidence_path = "/memories/projects/source.md"
        db.add(MemoryFile(user_id=1, path=evidence_path, content="# Source\nABC243 class notes."))
        await db.commit()
        with pytest.raises(MemorySchemaViolation, match="literal evidence"):
            await mutate_file(db, user_id=1, path="/memories/academic/courses/2026-2027-fall/ABC243.md",
                before=None, after=_new_course("ABC243", "Invented name"), author="ultron",
                action="course_memory", managed=True,
                evidence=[{"ref": evidence_path, "quote": "ABC243 class notes."}])
    reviewer.assert_not_called()


async def test_course_tool_does_not_ignore_a_conflicting_name(sessions):
    async with sessions() as db:
        db.add(MemoryFile(user_id=1, path="/memories/academic/courses/2026-2027-fall/ABC243.md",
                          content=_new_course("ABC243", "General Accounting")))
        await db.commit()
        result = await CourseMemorySkill().execute({"term": "2026-2027-fall", "course_code": "ABC243",
            "course_name": "Financial Accounting", "section": "Overview", "entry": "A note"},
            SimpleNamespace(db=db, user_id=1))
        assert "conflicts" in result and "Nothing saved" in result


def state(key, **changes):
    record = {"key": key, "summary": "Remote retainer active; minimum wage, central current work.",
              "status": "active", "verified_on": "2026-10-01", "review_on": "2026-10-01",
              "source": "message:1", **changes}
    return SimpleNamespace(path=f"/memories/states/{key}.md", content=encode(record))


def test_review_date_is_not_expiry_and_high_salience_survives_small_context():
    central = state("remote-retainer", salience="high")
    others = [state(f"other-{i}", summary="Unrelated ongoing situation.") for i in range(8)]
    view = render([*others, central], date(2026, 10, 1))
    excerpt = bounded_excerpt("/memories/current.md", view, 550)
    assert "Remote retainer active" in excerpt and len(excerpt) <= 550
    overdue = render([central], date(2026, 10, 2))
    assert "Needs confirmation" in overdue and "last reported" in overdue
    assert "Remote retainer active" in overdue
    ended = state("ended-onsite", ends_on="2026-09-19")
    assert freshness(parse(ended.content), date(2026, 10, 1)) == "expired_unconfirmed"
    assert "Known validity ended; outcome unconfirmed" in render([ended], date(2026, 10, 1))


async def test_state_head_monthly_compatibility_and_bounded_list(sessions, monkeypatch):
    monkeypatch.setattr("app.skills.memory_state.owner_today", lambda: date(2026, 10, 1))
    async with sessions() as db:
        for i in range(30):
            item = state(f"item-{i}", summary="Long summary " * 100)
            db.add(MemoryFile(user_id=1, path=item.path, content=item.content))
        newer = state("item-0", summary="Latest monthly edition.")
        db.add(MemoryFile(user_id=1, path="/memories/states/10-26/item-0.md", content=newer.content))
        await db.commit()
        import json
        ctx = SimpleNamespace(db=db, user_id=1)
        skill = MemoryStateSkill()
        page = await skill.execute({"operation": "list", "limit": 20}, ctx)
        assert len(page) < 6000
        parsed = json.loads(page)
        assert parsed["total"] == 30 and parsed["next_offset"] is not None
        get = json.loads(await skill.execute({"operation": "get", "key": "item-0"}, ctx))
        assert get["path"] == "/memories/states/10-26/item-0.md"
        assert "Latest monthly edition" in get["content"] and get["freshness"] == "current"


async def test_state_evidence_resolves_archive_but_rejects_projection_capsule(sessions):
    from app.services.memory_owner_review import preserve_source
    async with sessions() as db:
        original = owner_source()
        original.update(key="archived-job", original_path="/memories/.archive/old-employment.md", classification="historical")
        ident = await preserve_source(db, 1, original, "p")
        await db.commit()
        await verify_source(db, 1, original["original_path"])
        await verify_source(db, 1, f"source:{ident}")
        original.update(key="archived-state", original_path="/memories/states/old-job.md")
        state_source = await preserve_source(db, 1, original, "p")
        await db.commit()
        with pytest.raises(ValueError):
            await verify_source(db, 1, f"source:{state_source}")


async def test_explicit_owner_name_correction_preserves_notes_and_is_not_general_edit(sessions, monkeypatch):
    from app.models.message import Message
    from app.models.session import Session
    reviewer = AsyncMock(return_value="Owner confirmed the exact course identity.")
    monkeypatch.setattr("app.services.memory_admission.admit", reviewer)
    async with sessions() as db:
        session = Session(user_id=1, agent_id="ultron", triggered_by="user", model_used="test")
        db.add(session); await db.flush()
        db.add(Message(session_id=session.id, role="user", content="ABC243 General Accounting dersi."))
        path = "/memories/academic/courses/2026-2027-fall/ABC243.md"
        before = _new_course("ABC243", "Financial Accounting") + "\nA unique old lecture note.\n"
        db.add(MemoryFile(user_id=1, path=path, content=before))
        await db.commit()
        ctx = SimpleNamespace(db=db, user_id=1, session_id=session.id,
                              agent_id="ultron", request_id="identity-1", model="test")
        result = await CourseMemorySkill().execute({"operation": "confirm_identity",
            "term": "2026-2027-fall", "course_code": "ABC243", "course_name": "General Accounting",
            "evidence": [{"ref": "message:latest", "quote": "ABC243 General Accounting dersi."}]}, ctx)
        assert result.startswith("Recorded in")
        current = (await db.execute(select(MemoryFile.content).where(MemoryFile.path == path))).scalar_one()
        assert current.startswith("# ABC243 — General Accounting") and "unique old lecture note" in current
        revision = (await db.execute(select(MemoryRevision))).scalar_one()
        assert revision.before == before
        assert reviewer.await_count == 1


async def test_legacy_course_document_cannot_authorize_name_correction(sessions, monkeypatch):
    reviewer = AsyncMock(return_value="Approved")
    monkeypatch.setattr("app.services.memory_admission.admit", reviewer)
    async with sessions() as db:
        path = "/memories/academic/courses/2026-2027-fall/ABC243.md"
        before = _new_course("ABC243", "General Accounting")
        db.add(MemoryFile(user_id=1, path=path, content=before + "\nABC243 Financial Accounting, unverified legacy note.\n"))
        await db.commit()
        ctx = SimpleNamespace(db=db, user_id=1, session_id=1,
                              agent_id="ultron", request_id="identity-2", model="test")
        result = await CourseMemorySkill().execute({"operation": "confirm_identity",
            "term": "2026-2027-fall", "course_code": "ABC243", "course_name": "Financial Accounting",
            "evidence": [{"ref": path, "quote": "ABC243 Financial Accounting"}]}, ctx)
        assert "literal owner statement" in result
        assert not (await db.execute(select(MemoryRevision))).scalars().all()
    reviewer.assert_not_called()


async def test_offline_runner_accepts_logical_backup_and_resumes(tmp_path):
    import sqlite3
    from sqlalchemy import create_engine
    from scripts.apply_owner_memory_review import run
    from app.services.memory_cleanup import file_hash
    source = tmp_path / "source.db"
    output = tmp_path / "reviewed.db"
    engine = create_engine("sqlite:///" + source.as_posix())
    Base.metadata.create_all(engine)
    engine.dispose()
    before = _new_course("ABC243", "Financial Accounting")
    path = "/memories/academic/courses/2026-2027-fall/ABC243.md"
    with sqlite3.connect(source) as db:
        db.execute("INSERT INTO users VALUES (1,'Owner','Europe/Istanbul','2026-10-01')")
        db.execute("INSERT INTO memory_files VALUES (1,1,?,?,'2026-10-01')", (path, before))
    fingerprint = file_hash(source)
    plan = {"user_id": 1, "sources": [owner_source()], "units": [{"path": path,
        "before_hash": version(before), "after": before.replace("Financial Accounting", "General Accounting", 1),
        "evidence": [{"key": owner_source()["key"], "quote": owner_source()["content"]}]}]}
    first = await run(source, output, plan)
    second = await run(source, output, plan)
    assert first["integrity"] == "ok" and second["units"][0]["resumed"]
    assert file_hash(source) == fingerprint


async def test_root_tree_and_ui_projection_hide_only_replaced_monoliths(sessions):
    from app.services.memory_catalog import active_corpus_files
    from app.services.memory_states import project_files
    from app.services.memory_store import is_owner_editable
    async with sessions() as db:
        legacy = MemoryFile(user_id=1, path="/memories/wellness.md", content="# Wellness\nOld unique detail.")
        modern = MemoryFile(user_id=1, path="/memories/wellness/09-26/profile.md", content="# Profile\nCurrent profile.")
        other_owner = MemoryFile(user_id=2, path="/memories/wellness.md", content="# Wellness\nOther owner's only record.")
        internal = MemoryFile(user_id=1, path="/memories/.archive/nested/original.md", content="Historical original.")
        db.add_all([legacy, modern, other_owner, internal]); await db.commit()
        projected = active_corpus_files([legacy, modern, other_owner, internal])
        assert legacy not in projected and internal not in projected
        assert other_owner in projected and modern in projected
        assert [f.path for f in project_files([legacy, modern], date(2026, 10, 1))] == [modern.path]
        assert not is_owner_editable(legacy.path)
        ctx = SimpleNamespace(db=db, user_id=1)
        tree = await MemorySkill().execute({"command": "view", "path": "/memories"}, ctx)
        assert "/memories/wellness.md" not in tree and "profile.md" in tree and "original.md" not in tree
        exact = await MemorySkill().execute({"command": "view", "path": legacy.path}, ctx)
        assert "Historical legacy document" in exact and "Old unique detail" in exact


async def test_large_directory_read_has_continuation_and_no_unbounded_payload(sessions):
    async with sessions() as db:
        db.add_all([MemoryFile(user_id=1, path=f"/memories/projects/09-26/project-{i:04d}.md", content="# Project") for i in range(500)])
        await db.commit()
        result = await MemorySkill().execute({"command": "view", "path": "/memories/projects"}, SimpleNamespace(db=db, user_id=1))
        assert len(result) <= 6500 and "Continue with view_range" in result


def test_confirmation_requires_owner_authority_even_for_new_course():
    from app.services.course_identity import check_course_identity
    with pytest.raises(ValueError, match="literal owner statement"):
        check_course_identity("/memories/academic/courses/2026-2027-fall/ABC243.md",
            None, _new_course("ABC243", "General Accounting"),
            [{"quote": "ABC243 General Accounting", "source_authority": "document"}],
            allow_name_correction=True)


def test_state_heads_parse_once_per_edition(monkeypatch):
    from app.services import memory_states
    original = memory_states.parse
    calls = []
    def counted(content):
        calls.append(content)
        return original(content)
    monkeypatch.setattr(memory_states, "parse", counted)
    files = [state(f"item-{i}") for i in range(100)]
    heads, invalid = memory_states.state_heads(files)
    assert len(heads) == 100 and not invalid and len(calls) == 100


def test_mismatched_state_path_is_not_a_current_fact():
    item = state("actual-key")
    item.path = "/memories/states/another-key.md"
    output = render([item], date(2026, 10, 1))
    assert "Invalid state record" in output and "Remote retainer active" not in output


async def test_offline_review_refuses_source_hardlink(tmp_path):
    import os
    from scripts.apply_owner_memory_review import run
    source, output = tmp_path / "source.db", tmp_path / "alias.db"
    source.write_bytes(b"untouched input")
    os.link(source, output)
    with pytest.raises(ValueError, match="In-place"):
        await run(source, output, {})
    assert source.read_bytes() == b"untouched input"
