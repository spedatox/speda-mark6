"""A chat's durable desk cannot follow a later global picker selection.

Codex reference: state/src/model/thread_metadata.rs stores the thread cwd.
Exercise real SQLite transactions and the persistent workshop, including two
simultaneous first selections for chats in the same private project.
"""
import asyncio
import base64
import json

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401
from app.core.context import AgentContext
from app.database import Base, _apply_additive_migrations
from app.models.message import Message
from app.models.project import Project
from app.models.session import Session
from app.models.user import User
from app.services.workspaces import WorkspaceError, WorkspaceService
from app.skills.workshop import WorkshopUpdateSkill
from app.services.engineering_inputs import EngineeringInputService
from app.models.engineering_input import EngineeringInput


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'igor.db'}", poolclass=NullPool)

    @event.listens_for(engine.sync_engine, "connect")
    def pragmas(connection, _):
        cursor = connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=15000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add(User(id=1, name="owner", timezone="UTC"))
        await db.commit()
    yield maker
    await engine.dispose()


@pytest.fixture
def desks(tmp_path):
    root = tmp_path / "workspaces"
    a, b = root / "a", root / "b"
    a.mkdir(parents=True)
    b.mkdir()
    return root, a, b


async def chat(db, *, project_id=None, historical=False):
    session = Session(user_id=1, agent_id="optimus", model_used="test", triggered_by="user", project_id=project_id)
    db.add(session)
    await db.commit()
    if historical:
        db.add(Message(session_id=session.id, role="user", content=[{"type": "text", "text": "previous work"}]))
        await db.commit()
    return session.id


def ctx(db, session_id, **overrides):
    return AgentContext(**{
        "agent_id": "optimus", "user_id": 1, "session_id": session_id,
        "request_id": "test-desk", "triggered_by": "user", "trigger_payload": {},
        "output_mode": "respond", "model": "test", "system_prompt": "",
        "conversation_history": [], "db": db, **overrides,
    })


async def test_reload_keeps_each_chats_initial_desk(factory, desks):
    root, a, b = desks
    service = WorkspaceService(str(root))
    async with factory() as db:
        first, second = await chat(db), await chat(db)
        selected_a = await service.prepare(ctx(db, first), workspace=str(a))
        selected_b = await service.prepare(ctx(db, second), workspace=str(b))
    async with factory() as db:
        reopened = ctx(db, first)
        assert await WorkspaceService(str(root)).prepare(reopened, workspace=str(b)) == selected_a
        assert reopened.extra["cwd"] == str(a.resolve())
        assert reopened.workshop_project_id == selected_a.project_id
        with pytest.raises(WorkspaceError, match="another desk"):
            await service.prepare(reopened, project_id=selected_b.project_id, explicit=True)
        assert reopened.extra["cwd"] == str(a.resolve())


async def test_historical_chat_needs_explicit_selection(factory, desks, monkeypatch):
    root, a, _ = desks
    monkeypatch.setattr("app.execution.forge.settings.forge_workspace_root", str(root))
    service = WorkspaceService(str(root))
    async with factory() as db:
        session_id = await chat(db, historical=True)
        context = ctx(db, session_id, extra={"cwd": str(a)})
        assert await service.prepare(context, workspace=str(a)) is None
        assert "cwd" not in context.extra
        assert context.extra["workspace_binding_required"]
        assert not (root / ".forge").exists()
        selected = json.loads(await WorkshopUpdateSkill(str(root), workspace_service=service).execute(
            {"action": "select", "workspace": str(a)}, context,
        ))
        assert selected["selected_for_session"] == session_id
        assert "workspace_binding_required" not in context.extra
    async with factory() as db:
        assert (await db.get(Session, session_id)).workshop_project_id == selected["id"]


async def test_private_project_inherits_without_repointing_old_chat(factory, desks):
    root, a, b = desks
    service = WorkspaceService(str(root))
    async with factory() as db:
        project = Project(user_id=1, agent_id="optimus", name="Private", instructions="private instructions")
        db.add(project)
        await db.commit()
        old = await chat(db, project_id=project.id, historical=True)
        new = await chat(db, project_id=project.id)
        selected = await service.prepare(ctx(db, new), workspace=str(a))
        later = await chat(db, project_id=project.id)
        assert await service.prepare(ctx(db, later), workspace=str(b)) == selected
        old_context = ctx(db, old)
        assert await service.prepare(old_context, workspace=str(b)) is None
        assert old_context.extra["workspace_binding_required"]
        await db.refresh(project)
        assert project.workshop_project_id == selected.project_id
        assert project.instructions == "private instructions"


@pytest.mark.parametrize("overrides", [{"user_id": 2}, {"agent_id": "speda"}])
async def test_owner_agent_checked_before_inventory(factory, desks, overrides):
    root, a, _ = desks
    async with factory() as db:
        context = ctx(db, await chat(db), **overrides)
        with pytest.raises(WorkspaceError) as exc:
            await WorkspaceService(str(root)).prepare(context, workspace=str(a))
        assert exc.value.status_code == 404
        assert not (root / ".forge").exists()


async def test_cross_agent_private_project_cannot_be_linked(factory, desks):
    root, a, _ = desks
    async with factory() as db:
        project = Project(user_id=1, agent_id="speda", name="Private")
        db.add(project)
        await db.commit()
        context = ctx(db, await chat(db, project_id=project.id))
        with pytest.raises(WorkspaceError) as exc:
            await WorkspaceService(str(root)).prepare(context, workspace=str(a))
        assert exc.value.status_code == 404
        assert not (root / ".forge").exists()


async def test_missing_saved_desk_fails_closed(factory, desks):
    root, a, b = desks
    async with factory() as db:
        session_id = await chat(db)
        chosen = await WorkspaceService(str(root)).prepare(ctx(db, session_id), workspace=str(a))
        a.rmdir()
        with pytest.raises(WorkspaceError, match="unavailable"):
            await WorkspaceService(str(root)).prepare(ctx(db, session_id), workspace=str(b))
        assert (await db.get(Session, session_id)).workshop_project_id == chosen.project_id


async def test_concurrent_project_selection_commits_one_consistent_binding(factory, desks):
    root, a, b = desks
    async with factory() as db:
        project = Project(user_id=1, agent_id="optimus", name="Shared private project")
        db.add(project)
        await db.commit()
        project_id = project.id
        first, second = await chat(db, project_id=project_id), await chat(db, project_id=project_id)
    ready = asyncio.Event()
    arrivals = 0

    class RacingService(WorkspaceService):
        async def _save(self, *args):
            nonlocal arrivals
            arrivals += 1
            if arrivals == 2:
                ready.set()
            await asyncio.wait_for(ready.wait(), timeout=10)
            return await super()._save(*args)

    service = RacingService(str(root))

    async def bind(session_id, path):
        async with factory() as db:
            return await service.prepare(ctx(db, session_id), workspace=str(path))

    results = await asyncio.gather(bind(first, a), bind(second, b), return_exceptions=True)
    winners = [r for r in results if not isinstance(r, BaseException)]
    losers = [r for r in results if isinstance(r, BaseException)]
    assert len(winners) == len(losers) == 1
    assert isinstance(losers[0], WorkspaceError)
    async with factory() as db:
        assert (await db.get(Project, project_id)).workshop_project_id == winners[0].project_id
        values = list((await db.scalars(select(Session.workshop_project_id))).all())
        assert sorted(values, key=str) == sorted([winners[0].project_id, None], key=str)


async def test_migration_keeps_unbound_historical_rows_and_is_idempotent(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'old.db'}")
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE sessions (id INTEGER PRIMARY KEY, user_id INTEGER, agent_id TEXT, started_at TEXT, title TEXT)"))
            await conn.execute(text("CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT)"))
            await conn.execute(text("INSERT INTO sessions VALUES (1, 1, 'optimus', '2026-01-01', 'Keep me')"))
            await conn.execute(text("INSERT INTO projects VALUES (2, 'Private')"))
            await conn.run_sync(_apply_additive_migrations)
            await conn.run_sync(_apply_additive_migrations)
            assert (await conn.execute(text("SELECT title, workshop_project_id FROM sessions"))).one() == ("Keep me", None)
            assert (await conn.execute(text("SELECT name, workshop_project_id FROM projects"))).one() == ("Private", None)
    finally:
        await engine.dispose()


async def test_originals_survive_new_chat_on_same_desk_and_keep_agent_boundary(factory, desks):
    from forge.artifacts import ArtifactStore
    root, a, b = desks
    workspaces = WorkspaceService(str(root))
    inputs = EngineeringInputService(str(root), workspaces)
    async with factory() as db:
        first = ctx(db, await chat(db))
        await workspaces.prepare(first, workspace=str(a))
        upload = [{"name": "../spec.bin", "media_type": "application/octet-stream", "data": base64.b64encode(b"\x00\xfforiginal").decode()}]
        await inputs.accept(first, upload)
        await inputs.accept(first, upload)  # same bytes/name do not duplicate refs
        refs = first.extra["forge_inputs"]
        assert len(refs) == 1
        assert refs[0]["name"] == "spec.bin"
        assert "data" not in refs[0]
        assert len((await db.scalars(select(EngineeringInput))).all()) == 1
        other_desk = ctx(db, await chat(db))
        await workspaces.prepare(other_desk, workspace=str(b))
        await inputs.restore(other_desk)
        assert other_desk.extra["forge_inputs"] == []
        same_desk = ctx(db, await chat(db))
        await workspaces.prepare(same_desk, workspace=str(a))
        await EngineeringInputService(str(root), workspaces).restore(same_desk)
        assert same_desk.extra["forge_inputs"] == refs
        other_agent = Session(user_id=1, agent_id="speda", model_used="test", triggered_by="user")
        db.add(other_agent)
        await db.commit()
        context = ctx(db, other_agent.id, agent_id="speda")
        await workspaces.prepare(context, workspace=str(a))
        await inputs.restore(context)
        assert context.extra["forge_inputs"] == []
    assert ArtifactStore(root).read(refs[0]["artifact_hash"]) == b"\x00\xfforiginal"
    assert not (a / ".forge").exists()  # authoritative bytes outside worker mount


async def test_corrupt_original_fails_closed_and_bad_base64_is_not_recorded(factory, desks):
    root, a, _ = desks
    workspaces = WorkspaceService(str(root))
    inputs = EngineeringInputService(str(root), workspaces)
    async with factory() as db:
        context = ctx(db, await chat(db))
        await workspaces.prepare(context, workspace=str(a))
        with pytest.raises(WorkspaceError) as exc:
            await inputs.accept(context, [{"name": "bad", "data": "invalid!"}])
        assert exc.value.status_code == 422
        assert list((await db.scalars(select(EngineeringInput))).all()) == []
        await inputs.accept(context, [{"name": "valid", "data": base64.b64encode(b"original").decode()}])
        digest = context.extra["forge_inputs"][0]["artifact_hash"]
        (root / ".forge" / "artifacts" / digest[:2] / digest).write_bytes(b"tampered")
        with pytest.raises(WorkspaceError, match="missing or corrupt"):
            await inputs.restore(ctx(db, context.session_id))


async def test_input_persistence_failure_does_not_advertise_a_reference(factory, desks, monkeypatch):
    root, a, _ = desks
    workspaces = WorkspaceService(str(root))
    inputs = EngineeringInputService(str(root), workspaces)
    async with factory() as db:
        context = ctx(db, await chat(db))
        session_id = context.session_id
        await workspaces.prepare(context, workspace=str(a))

        async def fail_commit():
            raise RuntimeError("database is full")

        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="full"):
            await inputs.accept(context, [{"name": "input", "data": base64.b64encode(b"keep original").decode()}])
        assert not context.extra.get("forge_inputs")
    async with factory() as db:
        assert list((await db.scalars(select(EngineeringInput))).all()) == []
        assert await db.get(Session, session_id) is not None


async def test_selecting_an_unbound_chat_retains_pending_originals(factory, desks):
    root, a, _ = desks
    workspaces = WorkspaceService(str(root))
    inputs = EngineeringInputService(str(root), workspaces)
    async with factory() as db:
        context = ctx(db, await chat(db, historical=True))
        await inputs.accept(context, [{"name": "spec", "data": base64.b64encode(b"requirements").decode()}])
        assert "data" in context.extra["forge_inputs"][0]
        assert not (root / ".forge" / "artifacts").exists()
        result = await WorkshopUpdateSkill(str(root), workspace_service=workspaces, input_service=inputs).execute(
            {"action": "select", "workspace": str(a)}, context,
        )
        assert "selected_for_session" in result
        assert "artifact_hash" in context.extra["forge_inputs"][0]
        assert "data" not in context.extra["forge_inputs"][0]


async def test_task_cannot_repoint_bound_chat_or_start_for_invalid_input_ids(factory, desks):
    from unittest.mock import AsyncMock
    from app.core.registry import CapabilityRegistry
    from app.legion.runner import LegionRunner
    root, a, b = desks
    workspaces = WorkspaceService(str(root))
    async with factory() as db:
        context = ctx(db, await chat(db))
        original = await workspaces.prepare(context, workspace=str(a))
        other = await workspaces.prepare(ctx(db, await chat(db)), workspace=str(b))
        runner = LegionRunner(object(), CapabilityRegistry(), None, workspace_service=workspaces)
        runner._loop = AsyncMock()
        result = await runner.run_worker({
            "description": "work", "prompt": "go", "legionnaire": "autobot",
            "workshop_project_id": other.project_id,
        }, context)
        assert "Refused" in result
        assert context.workshop_project_id == original.project_id
        assert context.extra["cwd"] == str(a.resolve())
        runner._loop.assert_not_awaited()
        assert "Refused" in await runner.run_worker({"description": "work", "prompt": "go", "input_ids": [{}]}, context)
        runner._loop.assert_not_awaited()


async def test_http_chat_reloads_its_desk_and_inputs_before_launch(factory, desks):
    from types import SimpleNamespace
    from fastapi import BackgroundTasks
    from app.core.session_manager import SessionManager
    from app.routers.chat import _run_chat
    from app.schemas.chat import ChatRequest, DocumentAttachment

    root, a, b = desks
    service = WorkspaceService(str(root))
    inputs = EngineeringInputService(str(root), service)
    captured = []

    class Turns:
        def knows(self, request_id):
            return False

        def start(self, *, context, **kwargs):
            captured.append(context)
            return context.request_id

        async def subscribe(self, request_id):
            if False:
                yield

    class Profile:
        agent_id = "optimus"
        external_backend = False

        def allocate_model(self, triggered_by):
            return "test:model"

        def background_model(self, model):
            return model

    state = SimpleNamespace(
        orchestrator=object(), session_manager=SessionManager(),
        profiles=SimpleNamespace(get=lambda agent_id: Profile()), turns=Turns(),
        workspaces=service, engineering_inputs=inputs, agent_proxy=None,
    )
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    async with factory() as db:
        first = await chat(db)
        await _run_chat(request, "optimus", ChatRequest(
            message="use specification", session_id=first, cwd=str(a), documents=[
                DocumentAttachment(name="spec.txt", media_type="text/plain", data=base64.b64encode(b"requirements").decode()),
            ],
        ), BackgroundTasks(), db)
    state.workspaces = WorkspaceService(str(root))
    state.engineering_inputs = EngineeringInputService(str(root), state.workspaces)
    async with factory() as db:
        await _run_chat(request, "optimus", ChatRequest(message="continue", session_id=first, cwd=str(b)), BackgroundTasks(), db)
    assert captured[0].workshop_project_id == captured[1].workshop_project_id
    assert captured[1].extra["cwd"] == str(a.resolve())
    assert captured[0].extra["forge_inputs"] == captured[1].extra["forge_inputs"]
    assert "requirements" in str(captured[1].conversation_history)


async def test_coordinator_artifacts_cannot_be_selected_as_a_desk(factory, desks):
    root, _, _ = desks
    metadata = root / ".forge" / "artifacts"
    metadata.mkdir(parents=True)
    async with factory() as db:
        context = ctx(db, await chat(db))
        with pytest.raises(WorkspaceError, match="coordinator metadata"):
            await WorkspaceService(str(root)).prepare(context, workspace=str(metadata))
        assert context.workshop_project_id is None
        assert "cwd" not in context.extra


async def test_deleting_chat_removes_input_references_and_preserves_original_bytes(factory, desks):
    from forge.artifacts import ArtifactStore
    from sqlalchemy import delete
    root, a, _ = desks
    workspaces = WorkspaceService(str(root))
    inputs = EngineeringInputService(str(root), workspaces)
    async with factory() as db:
        context = ctx(db, await chat(db))
        await workspaces.prepare(context, workspace=str(a))
        await inputs.accept(context, [{"name": "spec", "data": base64.b64encode(b"original").decode()}])
        digest = context.extra["forge_inputs"][0]["artifact_hash"]
        await db.execute(delete(Session).where(Session.id == context.session_id))
        await db.commit()
        assert list((await db.scalars(select(EngineeringInput))).all()) == []
    assert ArtifactStore(root).read(digest) == b"original"
