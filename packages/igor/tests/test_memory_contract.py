# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
import json
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

import app.models  # register foreign key targets
from app.database import Base
from app.models.memory_file import MemoryFile
from app.models.memory_revision import MemoryRevision
from app.services.memory_states import encode, render, parse, project_files, version
from app.services.memory_store import mutate_file, MemoryWriteConflict
from app.services.memory_schema import check_write, MemorySchemaViolation, max_bytes_for
from app.services.memory_spec import route_ledger, injected_paths
from app.services.memory_audit import coverage, record_review


@pytest.fixture
async def sessions():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def state(**kwargs):
    return dict(key="pending-application", summary="Application is awaiting a response.",
                status="waiting", verified_on="2026-09-09", review_on="2026-09-12",
                source="/memories/academic/erasmus.md", **kwargs)


def file(path, content):
    return SimpleNamespace(path=path, content=content, updated_at=datetime.now(timezone.utc))


def test_states_expire_to_unknown_without_claiming_completion():
    f = file("/memories/states/pending-application.md", encode(state()))
    assert "Application is awaiting" in render([f], date(2026, 9, 12))
    overdue = render([f], date(2026, 9, 13))
    assert "Application is awaiting" not in overdue
    assert "Review overdue" in overdue
    assert parse(f.content)["status"] == "waiting"


def test_terminal_states_do_not_enter_current_and_plans_stay_plans():
    r = state()
    r.update(status="completed", closed_on="2026-09-10")
    assert "Application is awaiting" not in render([file("/memories/states/x.md", encode(r))], date(2026, 9, 10))
    r.update(status="planned", starts_on="2026-09-11")
    r.pop("closed_on")
    view = render([file("/memories/states/x.md", encode(r))], date(2026, 9, 10))
    assert "## Active / waiting\n\n(none recorded)" in view
    assert "status: planned" in view


@pytest.mark.parametrize("changes", [{"review_on":"2026-02-30"}, {"review_on":"tomorrow"},
    {"status":"done"}, {"key":"../escape"}, {"status":"completed"},
    {"starts_on":"2026-09-12", "ends_on":"2026-09-11"}])
def test_state_contract_rejects_invalid_lifecycles(changes):
    r = state()
    r.update(changes)
    with pytest.raises(ValueError):
        encode(r)


def test_projection_does_not_mutate_storage_and_legacy_is_not_silently_erased():
    original = file("/memories/current.md", "# Old\n\nSomething happened.")
    projected = project_files([original], date(2026, 9, 9))
    assert "Legacy snapshot" in projected[0].content
    assert original.content == "# Old\n\nSomething happened."
    original.content = "# Current\n\n<!-- state-projection-v1 -->"
    assert "Computed" in project_files([original], date(2026, 9, 9))[0].content
    assert "Computed" not in original.content


@pytest.mark.parametrize("path,author", [("/memories/current.md", "orion"),
    ("/memories/current.md", "speda"), ("/memories/current.md", "owner"),
    ("/memories/states/x.md", "orion"), ("/memories/.archive/finance.md", "speda"),
    ("/memories/log.md", "orion")])
def test_raw_writes_cannot_bypass_managed_surfaces(path, author):
    with pytest.raises(MemorySchemaViolation):
        check_write(path=path, before="", after="# x\n", is_create=True, author=author)


def test_refusal_does_not_advertise_shared_file_escape_hatch():
    with pytest.raises(MemorySchemaViolation) as exc:
        check_write(path="/memories/finance/notes.md", before="", after="# Notes\n", is_create=True, author="atomix")
    assert "do not reroute" in str(exc.value)
    assert "shared ones" not in str(exc.value)


def test_event_month_and_real_date_are_enforced():
    assert route_ledger("events", "2026-09-09") == ("/memories/events/2026-09.md", "")
    assert route_ledger("events", "2026-02-30")[1]
    assert route_ledger("/memories/events/2026-08.md", "2026-09-09")[1]


def test_new_dossier_topics_are_injected_and_caps_match_spec():
    existing = {"/memories/dossier/likes.md", "/memories/dossier/news-preferences.md"}
    assert "/memories/dossier/news-preferences.md" in injected_paths(existing)
    assert max_bytes_for("/memories/current.md") == 6000
    assert max_bytes_for("/memories/owner.md") == 24000


async def test_stale_writer_cannot_overwrite_another_agent_or_create_revision(sessions):
    async with sessions() as db:
        await mutate_file(db, user_id=1, path="/memories/projects/demo.md", before=None,
                          after="# Demo\n\nOriginal\n", author="speda", action="create")
        stale = "# Demo\n\nOriginal\n"
        await mutate_file(db, user_id=1, path="/memories/projects/demo.md", before=stale,
                          after="# Demo\n\nNew fact\n", author="optimus", action="edit")
        with pytest.raises(MemoryWriteConflict):
            await mutate_file(db, user_id=1, path="/memories/projects/demo.md", before=stale,
                              after="# Demo\n\nLost update\n", author="speda", action="edit")
        assert (await db.execute(select(MemoryFile.content))).scalar_one() == "# Demo\n\nNew fact\n"
        assert len((await db.execute(select(MemoryRevision))).scalars().all()) == 2


async def test_deletion_enforces_domain_ownership(sessions):
    async with sessions() as db:
        db.add(MemoryFile(user_id=1, path="/memories/finance/custom.md", content="# Custom\n"))
        await db.commit()
        with pytest.raises(MemorySchemaViolation):
            await mutate_file(db, user_id=1, path="/memories/finance/custom.md", before="# Custom\n",
                              after=None, author="atomix", action="delete")


async def test_audit_coverage_cannot_call_unseen_or_changed_memory_clean(sessions):
    async with sessions() as db:
        f = MemoryFile(user_id=1, path="/memories/projects/demo.md", content="# Demo\n\nDescription.\n")
        db.add(f)
        await db.commit()
        c = await coverage(db, 1)
        assert len(c["pending_review"]) == 1
        await record_review(db, user_id=1, author="orion", path=f.path,
                            fingerprint=version(f.content), findings=[], rationale="Checked project scope, time and evidence.")
        assert (await coverage(db, 1))["verdict"] == "reviewed"
        old = version(f.content)
        f.content += "\nChanged.\n"
        await db.commit()
        assert (await coverage(db, 1))["verdict"] == "review_required"
        with pytest.raises(ValueError):
            await record_review(db, user_id=1, author="orion", path=f.path,
                                fingerprint=old, findings=[], rationale="Stale review")


def test_old_scheduled_audit_cannot_restore_disabled_architecture():
    from app.core.trigger_runner import build_seed
    seed = build_seed({"job":"memory_audit", "intent":"obsolete compose instructions"}, "silent")
    assert "obsolete compose instructions" not in seed
    assert "pending_review" in seed
    assert "memory_audit(operation=" in seed


async def test_state_tool_persists_only_with_existing_evidence_and_version(sessions):
    from app.skills.memory_state import MemoryStateSkill
    async with sessions() as db:
        context = SimpleNamespace(db=db, user_id=1, agent_id="speda", session_id=1, request_id="test")
        args = {**state(), "operation":"put", "version":"new"}
        args.pop("verified_on")
        args["review_on"] = "2099-01-01"
        skill = MemoryStateSkill()
        assert "does not exist" in await skill.execute(args, context)
        db.add(MemoryFile(user_id=1, path=args["source"], content="# Erasmus\n\nPending application.\n"))
        await db.commit()
        result = json.loads(await skill.execute(args, context))
        assert result["status"] == "waiting"
        assert "stale version" in await skill.execute(args, context)


async def test_event_tool_creates_month_and_keeps_current_untouched(sessions):
    from app.skills.memory_write import LedgerAppendSkill
    async with sessions() as db:
        context = SimpleNamespace(db=db, user_id=1, agent_id="speda", session_id=1, request_id="test")
        result = await LedgerAppendSkill().execute({"path":"events", "key":"2026-09-09", "lines":["Sent the application."]}, context)
        assert "Written to /memories/events/2026-09.md" in result
        f = (await db.execute(select(MemoryFile))).scalar_one()
        assert "## 2026-09-09" in f.content


def test_extraction_prompt_requires_owner_quote_and_excludes_assistant():
    from app.services.fact_extraction import _PROMPT, ungrounded_tokens
    prompt = _PROMPT.format(max_facts=5, user_message="I want to travel.")
    assert "ASSISTANT:" not in prompt
    assert "quote" in prompt
    assert ungrounded_tokens("Booked Paris for 2400", "I want to travel.")


async def test_migration_is_atomic_idempotent_and_preserves_original(sessions):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("migration", Path(__file__).parents[1] / "scripts/migrate_memory_contract.py")
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    path = "/memories/projects/demo.md"
    original = "# Demo\n\nOriginal.\n"
    async with sessions() as db:
        db.add(MemoryFile(user_id=1, path=path, content=original))
        await db.commit()
        plan = {"id":"test-migration", "user_id":1, "changes":[
            {"path":path, "expected_sha256":version(original), "content":"# Demo\n\nRepaired.\n"}]}
        assert not (await migration.apply_plan(db, plan))["applied"]
        assert (await db.execute(select(MemoryFile.content))).scalar_one() == original
        assert (await migration.apply_plan(db, plan, apply=True))["applied"]
        assert not (await migration.apply_plan(db, plan, apply=True))["pending"]
        archive = (await db.execute(select(MemoryFile).where(MemoryFile.path.startswith("/memories/.archive/")))).scalar_one()
        assert archive.content == original
        stale = {"id":"stale", "user_id":1, "changes":[
            {"path":"/memories/projects/new.md", "expected_sha256":None, "content":"# New\n"},
            {"path":path, "expected_sha256":version(original), "content":"# Demo\n\nWrong.\n"}]}
        with pytest.raises(ValueError):
            await migration.apply_plan(db, stale, apply=True)
        assert (await db.execute(select(MemoryFile).where(MemoryFile.path == "/memories/projects/new.md"))).scalar_one_or_none() is None


async def test_extraction_queue_keeps_each_turn_and_its_source(sessions):
    from app.models.message import Message
    from app.models.background_job import BackgroundJob
    from app.services import task_queue
    async with sessions() as db:
        first = Message(session_id=1, role="user", content="First fact")
        db.add(first)
        await db.commit()
        first_id = first.id
    with patch.object(task_queue, "AsyncSessionLocal", sessions), patch.object(task_queue, "POST_TURN_KINDS", ("extract_facts",)):
        assert await task_queue.enqueue_post_turn(session_id=1, request_id="first", user_id=1, model="test") == 1
        async with sessions() as db:
            second = Message(session_id=1, role="user", content="Second fact")
            db.add(second)
            await db.commit()
            second_id = second.id
        assert await task_queue.enqueue_post_turn(session_id=1, request_id="second", user_id=1, model="test") == 1
        assert await task_queue.enqueue_post_turn(session_id=1, request_id="first", user_id=1, model="test") == 0
    async with sessions() as db:
        jobs = (await db.execute(select(BackgroundJob).order_by(BackgroundJob.id))).scalars().all()
        assert [j.payload["source_message_id"] for j in jobs] == [first_id, second_id]


def test_finance_row_cannot_land_in_another_month():
    from app.services.memory_write import ledger_append, WriteRejected
    with pytest.raises(WriteRejected, match="does not belong"):
        ledger_append("", path="/memories/finance/ledger/2026-09.md", key="2026-09",
                      section="Expenses", row=["2026-08-01", "Item", "10", "Note"])
