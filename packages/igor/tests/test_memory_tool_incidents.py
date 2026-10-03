"""Regressions for failed parallel reads, source citations and duplicate logs."""
import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.models
from app.core.context import AgentContext
from app.core.registry import CapabilityRegistry
from app.database import Base
from app.models.memory_file import MemoryFile
from app.models.memory_passage import MemoryPassage
from app.models.message import Message
from app.models.session import Session
from app.models.user import User
from app.services.memory_admission import resolve_evidence
from app.services.memory_states import parse
from app.skills.base import Skill
from app.skills.memory import _page
from app.skills.memory_state import MemoryStateSkill
from app.skills.memory_write import RegistryUpsertSkill
from app.skills.search_history import SearchHistorySkill
from app.skills.semantic_search import SemanticSearchSkill


@pytest.fixture
async def sessions(tmp_path, monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///" + (tmp_path / "incident.db").as_posix(), poolclass=NullPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add(User(id=1, name="Owner", timezone="Europe/Istanbul"))
        db.add(Session(id=1, user_id=1, agent_id="speda", triggered_by="user", model_used="unused"))
        db.add(Message(id=1, session_id=1, role="user", content="The dorm gym has a treadmill."))
        await db.commit()
    monkeypatch.setattr("app.services.memory_admission.admit", AsyncMock(return_value="Supported owner statement."))
    yield factory
    await engine.dispose()


def context(db, agent_id="speda"):
    return AgentContext(agent_id=agent_id, user_id=1, session_id=1, request_id="incident",
                        triggered_by="user", trigger_payload={}, output_mode="respond", model="unused",
                        system_prompt="", conversation_history=[], db=db, timezone="Europe/Istanbul")


async def test_parallel_reads_use_distinct_sessions_and_still_overlap(sessions):
    started, release, seen = asyncio.Event(), asyncio.Event(), []

    class Reader(Skill):
        name = "incident_read"
        read_only = True
        description = "Read the fixture. Use for the incident. Never write. Return the owner's name."
        input_schema = {}

        async def execute(self, args, ctx):
            seen.append(ctx.db)
            if len(seen) == 2:
                started.set()
            await release.wait()
            return (await ctx.db.execute(select(User.name).where(User.id == ctx.user_id))).scalar_one()

    registry = CapabilityRegistry()
    await registry.register_skill(Reader())
    async with sessions() as db:
        ctx = context(db)
        tasks = [asyncio.create_task(registry.execute("incident_read", {"key": i}, ctx)) for i in range(2)]
        try:
            await asyncio.wait_for(started.wait(), 2)
            assert seen[0] is not seen[1] and all(item is not db for item in seen)
        finally:
            release.set()
            results = await asyncio.gather(*tasks)
        assert results == ["Owner", "Owner"]


async def test_parallel_mutations_are_serial_and_rollback_does_not_break_next_tool(sessions):
    active, maximum = 0, 0

    class Writer(Skill):
        name = "incident_write"
        read_only = False
        description = "Update the fixture. Use for the incident. Never retrieve. Return the committed value."
        input_schema = {}

        async def execute(self, args, ctx):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            try:
                await asyncio.sleep(0.01)
                owner = await ctx.db.get(User, 1, populate_existing=True)
                if args.get("fail"):
                    owner.name = "Uncommitted"
                    await ctx.db.rollback()
                    return "Error: deliberate rollback"
                owner.name = args["value"]
                await ctx.db.commit()
                return owner.name
            finally:
                active -= 1

    registry = CapabilityRegistry()
    await registry.register_skill(Writer())
    async with sessions() as db:
        ctx = context(db)
        results = await asyncio.gather(registry.execute("incident_write", {"fail": True}, ctx),
                                       registry.execute("incident_write", {"value": "Committed"}, ctx))
        assert maximum == 1
        assert results == ["Error: deliberate rollback", "Committed"]
        assert (await db.get(User, 1, populate_existing=True)).name == "Committed"


async def test_read_finishing_after_mutation_cannot_repopulate_stale_memo():
    started, release = asyncio.Event(), asyncio.Event()
    state = {"value": "old", "reads": 0}

    class Reader(Skill):
        name = "slow_incident_read"
        read_only = True
        description = "Read a value. Use for the cache incident. Never write. Return a snapshot."
        input_schema = {}

        async def execute(self, args, ctx):
            state["reads"] += 1
            value = state["value"]
            if state["reads"] == 1:
                started.set()
                await release.wait()
            return value

    class Writer(Reader):
        name = "fast_incident_write"
        read_only = False

        async def execute(self, args, ctx):
            state["value"] = "new"
            return "Written"

    registry = CapabilityRegistry()
    await registry.register_skill(Reader())
    await registry.register_skill(Writer())
    from types import SimpleNamespace
    ctx = SimpleNamespace(extra={}, agent_id="speda", request_id="incident")
    task = asyncio.create_task(registry.execute(Reader.name, {}, ctx))
    await started.wait()
    try:
        await registry.execute(Writer.name, {}, ctx)
    finally:
        release.set()
    assert await task == "old"
    assert await registry.execute(Reader.name, {}, ctx) == "new"
    assert state["reads"] == 2


@pytest.mark.parametrize("args", [{"char_range": [0, 0], "view_range": [0, 0]},
                                  {"char_range": None, "view_range": None}])
def test_empty_optional_paging_fields_mean_default_read(args):
    output = _page("/memories/projects", "First line\nSecond line", args)
    assert "First line" in output and "Second line" in output


def test_real_paging_offsets_still_validate_and_do_not_expand():
    assert "characters 0:1:\nF" in _page("/memories/projects", "First line", {"char_range": [0, 1]})
    assert "requires" in _page("/memories/projects", "First line", {"char_range": [-1, 3]})
    assert "requires" in _page("/memories/projects", "First line", {"char_range": [3, 1]})


async def test_duplicate_empty_log_sections_do_not_fake_a_concurrent_create(sessions):
    before = "# Demo Club\n\nClub.\n\n## Log\n\n## Log\n\n## Log\n\n## Board\nMember.\n"
    async with sessions() as db:
        db.add(Message(id=3, session_id=1, role="user", content="A board member left the club."))
        db.add(MemoryFile(user_id=1, path="/memories/projects/09-26/demo-club.md", content=before))
        await db.commit()
        result = await RegistryUpsertSkill().execute({"kind": "project", "entity": "Demo Club",
            "event": "A board member left the club.", "when": "2026-10-03",
            "evidence": [{"ref": "message:3", "quote": "A board member left the club."}]}, context(db))
        assert result.startswith("Written to"), result
        doc = (await db.execute(select(MemoryFile))).scalar_one()
        assert doc.content.count("## Log") == 3 and "Member." in doc.content
        indexed = (await db.execute(select(MemoryPassage).where(MemoryPassage.retired.is_(False)))).scalars().all()
        assert len({part.id for part in indexed}) == len(indexed)


async def test_state_latest_source_is_pinned_to_the_evidenced_message(sessions):
    async with sessions() as db:
        db.add(Message(id=2, session_id=1, role="user", content="Save that gym environment."))
        await db.commit()
        result = await MemoryStateSkill().execute({"operation": "put", "key": "dorm-gym", "version": "new",
            "summary": "The dorm gym has a treadmill.", "status": "active", "source": "message:latest",
            "review_on": "2026-12-01", "evidence": [{"ref": "message:latest", "quote": "The dorm gym has a treadmill."}]}, context(db))
        assert "written" in json.loads(result)
        doc = (await db.execute(select(MemoryFile))).scalar_one()
        assert parse(doc.content)["source"] == "message:1"


async def test_both_history_readers_return_usable_original_message_ids(sessions):
    from types import SimpleNamespace
    async with sessions() as db:
        ctx = context(db)
        literal = await SearchHistorySkill().execute({"query": "treadmill"}, ctx)
        emb = SimpleNamespace(session_id=1, agent_id="speda", message_id=1,
                              created_at=datetime.now(timezone.utc))
        semantic = await SemanticSearchSkill()._render(ctx, [(emb, "Gym")], [1.0], [0], 0, "treadmill")
        for output in (literal, semantic):
            assert "message:1" in output
            assert "The dorm gym has a treadmill." in output
        evidence = await resolve_evidence(db, 1, [{"ref": "message:1", "quote": "The dorm gym has a treadmill."}])
        assert evidence[0]["source_authority"] == "owner_statement"
