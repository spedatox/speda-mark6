"""Durable worker visibility, stale controls, safe steering and owned cleanup.

Uses a real WAL/FK SQLite store and the real Legion adapter / Forge runtime.
Only provider output and platform subprocess execution are scripted.
"""
import asyncio
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import delete, event, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401
from app.config import settings
from app.core.context import AgentContext
from app.core.registry import CapabilityRegistry
from app.database import Base
from app.execution.forge import ForgeInterrupted
from app.legion.roster import LEGION_ROSTER
from app.legion.runner import LegionRunner, WorkerInterrupted
from app.models.session import Session
from app.models.user import User
from app.models.worker_execution import WorkerEvent, WorkerExecution
from app.services.worker_control import WorkerControlService, WorkerInbox
from app.services.workspaces import WorkspaceError


@pytest.fixture
async def harness(tmp_path):
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
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add_all([User(id=1, name="owner"), User(id=2, name="other")])
        await db.flush()
        row = Session(user_id=1, agent_id="optimus", model_used="test", triggered_by="user")
        db.add(row)
        await db.commit()
        sid = row.id
    root = tmp_path / "workspaces"
    root.mkdir()
    desk = root / "desk"
    desk.mkdir()
    context = AgentContext(agent_id="optimus", user_id=1, session_id=sid, request_id="parent-turn",
        triggered_by="user", trigger_payload={}, output_mode="respond", model="test", system_prompt="",
        conversation_history=[], db=None, extra={"cwd": str(desk)})
    service = WorkerControlService(factory, artifact_root=str(root))
    yield factory, service, context, root, desk
    await engine.dispose()


async def start(service, context, worker="autobot", execution_id=None):
    execution_id = execution_id or uuid.uuid4().hex
    await service.begin(execution_id, context=context, worker=LEGION_ROSTER[worker], model="test",
                        task="build and check", ui_run_id="tool-call", background=False)
    return execution_id


async def hold(service, execution_id, backend="forge"):
    signal = asyncio.Event()
    task = asyncio.create_task(asyncio.Event().wait())
    service.bind(execution_id, task, signal, backend)
    return task, signal


async def stop(service, execution_id, task):
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await service.executor_stopped(execution_id)


async def test_restart_preserves_events_inputs_and_unknown_runtime(harness):
    factory, service, context, root, _ = harness
    context.extra["forge_inputs"] = [{"input_id": "original", "artifact_hash": "a" * 64, "name": "spec.md"}]
    eid = await start(service, context)
    task, _ = await hold(service, eid)
    await service.append(eid, {"phase": "tool", "tool_call_id": "t1", "input": {"path": "main.py"}})
    await service.append(eid, {"phase": "tool_result", "tool_call_id": "t1", "full_result": "x" * 9000})
    receipt = await service.message(eid, "check README", user_id=1, message_id="b" * 32)
    assert receipt["status"] == "queued"
    restarted = WorkerControlService(factory, artifact_root=str(root))
    inspected = await restarted.inspect(eid, user_id=1)
    assert inspected["status"] == "unknown" and inspected["stored_status"] == "running"
    assert not inspected["executor_available"] and inspected["inputs"][0]["input_id"] == "original"
    assert inspected["messages"][0]["status"] == "queued"
    page = await restarted.events(eid, user_id=1, after=1)
    assert [ev["sequence"] for ev in page["events"]] == [2, 3, 4]
    assert len(page["events"][1]["full_result"]) == 9000
    with pytest.raises(WorkspaceError, match="Executor unavailable"):
        await restarted.interrupt(eid, user_id=1)
    with pytest.raises(WorkspaceError, match="live"):
        await restarted.message(eid, "again", user_id=1)
    await stop(service, eid, task)


async def test_owner_agent_scope_and_missing_cursor_errors(harness):
    _, service, context, _, _ = harness
    eid = await start(service, context)
    for operation in (service.inspect, service.events, service.interrupt):
        with pytest.raises(WorkspaceError) as error:
            await operation(eid, user_id=2)
        assert error.value.status_code == 404
    with pytest.raises(WorkspaceError, match="not found"):
        await service.inspect(eid, user_id=1, agent_id="speda")
    with pytest.raises(WorkspaceError, match="Invalid"):
        await service.inspect("ticket-1", user_id=1)
    with pytest.raises(WorkspaceError, match="ahead"):
        await service.events(eid, user_id=1, after=100)
    with pytest.raises(WorkspaceError, match="nonnegative"):
        await service.events(eid, user_id=1, after=-1)
    assert not await service.list(user_id=2)


async def test_message_retries_conflicts_boundary_and_terminal_fence(harness):
    _, service, context, _, _ = harness
    eid = await start(service, context)
    task, _ = await hold(service, eid)
    mid = "c" * 32
    receipts = await asyncio.gather(*[service.message(eid, "use the other file", user_id=1, message_id=mid) for _ in range(4)])
    assert all(r["message_id"] == mid and r["status"] == "queued" for r in receipts)
    with pytest.raises(WorkspaceError, match="different"):
        await service.message(eid, "different", user_id=1, message_id=mid)
    box = WorkerInbox(service, eid)
    assert await box.claim() == ["use the other file"]
    assert (await service.inspect(eid, user_id=1))["messages"][0]["status"] == "queued"
    transcript = [{"role": "user", "content": "go"}, {"role": "assistant", "content": "done"},
                  {"role": "user", "content": "use the other file"}]
    await box.record_boundary(transcript)
    assert await box.claim() == []
    await service.message(eid, "late correction", user_id=1, message_id="d" * 32)
    await service.finish(eid, "completed", "checked")
    assert (await service.message(eid, "use the other file", user_id=1, message_id=mid))["status"] == "boundary_recorded"
    with pytest.raises(WorkspaceError, match="live"):
        await service.message(eid, "new work", user_id=1)
    saved = await service.inspect(eid, user_id=1)
    assert [m["status"] for m in saved["messages"]] == ["boundary_recorded", "queued"]
    await stop(service, eid, task)


async def test_concurrent_events_are_gapless_and_completed_replay_survives_chat_delete(harness):
    factory, service, context, root, _ = harness
    eid = await start(service, context)
    sequences = await asyncio.gather(*[service.append(eid, {"phase": "text", "text": str(i)}) for i in range(20)])
    assert sorted(sequences) == list(range(2, 22))
    await service.finish(eid, "completed", "saved")
    async with factory() as db:
        await db.execute(delete(Session).where(Session.id == context.session_id))
        await db.commit()
    restarted = WorkerControlService(factory, artifact_root=str(root))
    replay = [ev async for ev in restarted.subscribe(eid, user_id=1, after=20)]
    assert [ev["sequence"] for ev in replay] == [21, 22]
    assert replay[-1]["status"] == "completed"


async def test_event_tail_has_no_replay_tail_gap(harness):
    _, service, context, _, _ = harness
    eid = await start(service, context)
    task, _ = await hold(service, eid)
    received, first = [], asyncio.Event()

    async def reader():
        async for ev in service.subscribe(eid, user_id=1):
            received.append(ev)
            first.set()
    reader_task = asyncio.create_task(reader())
    await asyncio.wait_for(first.wait(), 3)
    await service.append(eid, {"phase": "text", "text": "tail"})
    await service.finish(eid, "completed", "done")
    await asyncio.wait_for(reader_task, 3)
    assert [ev["sequence"] for ev in received] == [1, 2, 3]
    await stop(service, eid, task)


async def test_large_event_original_is_scoped_hash_verified_and_restart_readable(harness):
    factory, service, context, root, _ = harness
    eid = await start(service, context)
    original = {"phase": "tool_result", "full_result": "large " * 100000}
    await service.append(eid, original)
    page = await service.events(eid, user_id=1)
    digest = page["events"][-1]["event_artifact"]
    restarted = WorkerControlService(factory, artifact_root=str(root))
    assert json.loads(await restarted.event_artifact(eid, digest, user_id=1)) == original
    other = await start(service, context)
    with pytest.raises(WorkspaceError) as error:
        await service.event_artifact(other, digest, user_id=1)
    assert error.value.status_code == 404


async def test_inline_interrupt_is_targeted_parent_survives_and_cleanup_is_joined(harness):
    _, service, context, _, _ = harness
    runner = LegionRunner(object(), CapabilityRegistry(), None, worker_control=service)
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def loop(**kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()
    runner._loop = loop
    parent = asyncio.create_task(runner.run_worker({"prompt": "go", "legionnaire": "general"}, context, tool_call_id="inline-tool"))
    await asyncio.wait_for(entered.wait(), 3)
    eid = (await service.list(user_id=1))[0]["execution_id"]
    accepted = await service.interrupt(eid, user_id=1)
    assert accepted["accepted"]
    await asyncio.wait_for(cleaning.wait(), 3)
    await asyncio.gather(*[service.interrupt(eid, user_id=1) for _ in range(3)])
    assert not parent.done() and not parent.cancelling()
    release.set()
    with pytest.raises(WorkerInterrupted):
        await asyncio.wait_for(parent, 3)
    saved = await service.inspect(eid, user_id=1)
    assert saved["status"] == "interrupted" and not saved["executor_available"]
    assert runner._worker_count() == 0
    # A later run is a different ID even if the parent tool ID is reused.
    runner._loop = AsyncMock(return_value="done")
    assert await runner.run_worker({"prompt": "next"}, context, tool_call_id="inline-tool") == "done"
    current = (await service.list(user_id=1))[0]
    assert current["execution_id"] != eid
    assert not (await service.interrupt(eid, user_id=1))["accepted"]
    assert current["status"] == "completed"


async def test_two_forge_workers_targeted_signal_and_terminal_commit_precede_ui(harness):
    _, service, context, _, _ = harness
    runner = LegionRunner(object(), CapabilityRegistry(), None, worker_control=service)
    entered = asyncio.Queue()
    events = []

    async def loop(**kwargs):
        entered.put_nowait(kwargs)
        await kwargs["signal"].wait()
        raise ForgeInterrupted("stopped after teardown")
    runner._loop = loop

    async def ui(ev):
        if ev["phase"] == "finished":
            saved = await service.inspect(ev["execution_id"], user_id=1)
            assert saved["stored_status"] == "interrupted"
        events.append(ev)
    workers = [asyncio.create_task(runner.run_worker({"prompt": "go", "legionnaire": "autobot"}, context,
              tool_call_id=f"tool-{i}", emit=ui)) for i in range(2)]
    first, second = await asyncio.wait_for(entered.get(), 3), await asyncio.wait_for(entered.get(), 3)
    await service.interrupt(first["run_id"], user_id=1)
    assert first["signal"].is_set() and not second["signal"].is_set()
    await service.interrupt(second["run_id"], user_id=1)
    results = await asyncio.gather(*workers, return_exceptions=True)
    assert all(isinstance(r, ForgeInterrupted) for r in results)
    assert len(events) == 2 and all(ev["ok"] is False for ev in events)


async def test_admission_and_terminal_commit_fail_closed(harness, monkeypatch):
    _, service, context, _, _ = harness
    runner = LegionRunner(object(), CapabilityRegistry(), None, worker_control=service)
    runner._loop = AsyncMock(return_value="success")
    monkeypatch.setattr(service, "begin", AsyncMock(side_effect=OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        await runner.run_worker({"prompt": "go"}, context)
    runner._loop.assert_not_awaited()
    assert runner._worker_count() == 0
    monkeypatch.undo()
    monkeypatch.setattr(service, "finish", AsyncMock(side_effect=OSError("commit failed")))
    ui = []
    with pytest.raises(OSError, match="commit failed"):
        await runner.run_worker({"prompt": "go"}, context, emit=ui.append)
    saved = (await service.list(user_id=1))[0]
    assert saved["status"] == "unknown" and saved["stored_status"] == "running"
    assert not any(ev.get("ok") is True for ev in ui)


async def test_uncertain_workshop_claim_overrides_projection_and_is_not_released(harness, monkeypatch):
    from forge.workshop import Workshop
    _, service, context, root, desk = harness
    store = Workshop(root)
    runner = LegionRunner(object(), CapabilityRegistry(), None, worker_control=service)

    async def loop(**kwargs):
        store.claim(desk, kwargs["run_id"], "write")
        store.interrupt(kwargs["run_id"], "Cell teardown failed")
        raise RuntimeError("Cell teardown failed")
    runner._loop = loop
    with pytest.raises(RuntimeError, match="teardown"):
        await runner.run_worker({"prompt": "go", "legionnaire": "autobot"}, context)
    saved = (await service.list(user_id=1))[0]
    assert saved["status"] == "unknown" and store.run_status(saved["execution_id"])["status"] == "interrupted"
    with pytest.raises(ValueError, match="claimed"):
        store.claim(desk, "later", "new work")


async def test_background_ack_has_saved_execution_and_completion(harness):
    _, service, context, _, _ = harness
    runner = LegionRunner(object(), CapabilityRegistry(), None, worker_control=service)
    entered, release = asyncio.Event(), asyncio.Event()
    runner._log_start = AsyncMock(return_value=27)
    runner._log_finish = AsyncMock()

    async def loop(**kwargs):
        entered.set()
        await release.wait()
        return "checked"
    runner._loop = loop
    reply = await runner.run_worker({"prompt": "go", "run_in_background": True}, context)
    saved = (await service.list(user_id=1))[0]
    assert saved["execution_id"] in reply and saved["ticket_id"] == 27
    await asyncio.wait_for(entered.wait(), 3)
    release.set()
    await asyncio.gather(*list(runner._background))
    assert (await service.inspect(saved["execution_id"], user_id=1))["status"] == "completed"


@pytest.mark.parametrize("with_tool", [False, True])
async def test_real_warden_injects_durable_messages_at_legal_boundary(harness, tmp_path, with_tool):
    from forge.cell.base import CellPolicy
    from forge.cell.subprocess_cell import SubprocessCell
    from forge.model.scripted import ScriptedModel, tool_call
    from forge.warden.engine import Warden
    from forge.warden.filestate import FileStateCache
    from forge.warden.permissions import PermissionEngine
    from forge.warden.state import StopReason
    from forge.warden.tool import Tool, ToolContext, ToolResult
    from pydantic import BaseModel

    _, service, context, _, _ = harness
    eid = await start(service, context)
    task, _ = await hold(service, eid)
    await service.message(eid, "also check the README", user_id=1, message_id="e" * 32)

    class Args(BaseModel):
        pass
    class Ping(Tool):
        name, description, READ_ONLY = "ping", "read-only ping", True
        async def call(self, args, ctx):
            return ToolResult("pong")
    Ping.Args = Args
    cell = SubprocessCell(workspace=tmp_path, policy=CellPolicy())
    ctx = ToolContext(agent_id="worker", cell=cell, graph=None, files=FileStateCache(),
                      permissions=PermissionEngine(), network_allowed=False)

    def second(messages):
        assert "also check the README" in json.dumps(messages[-1])
        if with_tool:
            assert messages[-1]["content"][0]["type"] == "tool_result"
        return "done", []
    model = ScriptedModel([lambda m: ("summary", [tool_call("ping")] if with_tool else []), second])
    warden = Warden(system_prompt="", tools={"ping": Ping()}, model=model, ctx=ctx, inbox=WorkerInbox(service, eid))
    terminal = await warden.run("go")
    assert terminal.reason is StopReason.COMPLETED
    assert (await service.inspect(eid, user_id=1))["messages"][0]["status"] == "boundary_recorded"
    recorded = (await service.events(eid, user_id=1))["events"][-1]
    assert recorded["phase"] == "input_boundary"
    assert recorded["messages"][-1]["role"] == "user"
    await stop(service, eid, task)


async def test_routes_validate_missing_ids_and_sse_replay(harness):
    from app.middleware.auth import AuthMiddleware
    from app.routers.legion import router
    _, service, context, _, _ = harness
    app = FastAPI()
    app.state.worker_control = service
    app.include_router(router)
    app.add_middleware(AuthMiddleware)

    @app.exception_handler(WorkspaceError)
    async def workspace_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status_code)
    eid = await start(service, context)
    await service.finish(eid, "completed", "done")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/legion/executions")).status_code == 401
        headers = {"X-API-Key": settings.speda_api_key}
        assert (await client.get("/legion/executions/" + "f" * 32, headers=headers)).status_code == 404
        assert (await client.get(f"/legion/executions/{eid}/events?after=-1", headers=headers)).status_code == 400
        stream = await client.get(f"/legion/executions/{eid}/attach?after=1", headers=headers)
        assert stream.status_code == 200 and '"phase": "settled"' in stream.text
        invalid = await client.post(f"/legion/executions/{eid}/messages", headers=headers, json={"text": "", "message_id": "wrong"})
        assert invalid.status_code == 422


async def test_inspection_refreshes_within_same_parent_turn_and_workers_cannot_control_peers(harness):
    from app.skills.legion import LegionInspectSkill, LegionControlSkill
    _, service, context, _, _ = harness
    registry = CapabilityRegistry()
    await registry.register_skill(LegionInspectSkill(service))
    await registry.register_skill(LegionControlSkill(service))
    eid = await start(service, context)
    first = json.loads(await registry.execute("legion_inspect", {"execution_id": eid}, context))
    assert first["execution"]["stored_status"] == "running"
    await service.finish(eid, "completed", "fresh")
    second = json.loads(await registry.execute("legion_inspect", {"execution_id": eid}, context))
    assert second["execution"]["stored_status"] == "completed"
    assert registry.call_is_read_only("legion_inspect", {})
    runner = LegionRunner(object(), registry, None, worker_control=service)
    names = {t["name"] for t in runner._worker_tools(LEGION_ROSTER["general"], context)}
    assert "legion_control" not in names and "legion_inspect" not in names


@pytest.mark.parametrize("action", ["message", "interrupt"])
async def test_real_legion_forge_runtime_provider_and_workshop_controls(harness, monkeypatch, action):
    from forge.workshop import Workshop
    _, service, context, root, desk = harness
    monkeypatch.setattr(settings, "forge_workspace_root", str(root))
    monkeypatch.setattr(settings, "forge_cell_backend", "subprocess")
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    provider_cancelled = asyncio.Event()

    class Client:
        async def create_message(self, **kwargs):
            calls.append(json.loads(json.dumps(kwargs["messages"])))
            if len(calls) == 1:
                entered.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    provider_cancelled.set()
                    raise
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="checked")], stop_reason="end_turn", usage=None)
    runner = LegionRunner(Client(), CapabilityRegistry(), None, worker_control=service)
    ui = []
    parent = asyncio.create_task(runner.run_worker({"prompt": "review this project", "legionnaire": "forge_reviewer"},
                                context, tool_call_id="review-tool", emit=ui.append))
    await asyncio.wait_for(entered.wait(), 5)
    execution = (await service.list(user_id=1))[0]
    eid = execution["execution_id"]
    store = Workshop(root)
    assert store.run_status(eid)["status"] == "running"
    if action == "message":
        receipt = await service.message(eid, "also inspect README", user_id=1)
        assert receipt["status"] == "queued" and not parent.done()
        release.set()
        assert await asyncio.wait_for(parent, 5) == "checked"
        assert len(calls) == 2 and "also inspect README" in json.dumps(calls[-1][-1])
        assert (await service.inspect(eid, user_id=1))["messages"][0]["status"] == "boundary_recorded"
        assert store.run_status(eid)["status"] == "succeeded"
        assert ui[-1]["ok"] is True
    else:
        await service.interrupt(eid, user_id=1)
        with pytest.raises(ForgeInterrupted):
            await asyncio.wait_for(parent, 5)
        assert provider_cancelled.is_set() and len(calls) == 1
        assert store.run_status(eid)["status"] == "cancelled"
        assert (await service.inspect(eid, user_id=1))["status"] == "interrupted"
        assert ui[-1]["ok"] is False
    assert ui[-1]["execution_id"] == eid and ui[-1]["id"] == "review-tool"
    # A confirmed teardown, including a signal-driven interruption, releases
    # the claim. This is distinct from the uncertainty regression above.
    store.claim(desk, "new-execution", "can now enter safely")


async def test_debug_export_preserves_worker_graph_and_message_receipts(harness):
    from app.services.chat_history import debug_export, fold_subagent_event
    factory, service, context, _, _ = harness
    eid = await start(service, context)
    task, _ = await hold(service, eid)
    await service.message(eid, "instruction", user_id=1, message_id="a" * 32)
    await service.finish(eid, "unknown", "cleanup uncertain")
    async with factory() as db:
        payload = await debug_export(db, context.session_id)
    assert payload["worker_executions"][0]["parent_request_id"] == context.request_id
    assert payload["worker_events"][-1]["payload"]["status"] == "unknown"
    assert payload["worker_inputs"][0]["status"] == "queued"
    projection = fold_subagent_event(None, {"id": "tool", "execution_id": eid, "sequence": 3,
        "phase": "finished", "status": "unknown", "ok": False})
    assert projection["execution_id"] == eid and projection["status"] == "unknown"
    await stop(service, eid, task)


async def test_repeated_parent_cancel_joins_settlement_and_propagates_cancel(harness, monkeypatch):
    _, service, context, _, _ = harness
    runner = LegionRunner(object(), CapabilityRegistry(), None, worker_control=service)
    runner._loop = AsyncMock(return_value="loop complete")
    finishing, release = asyncio.Event(), asyncio.Event()
    real_finish = service.finish

    async def held_finish(*args):
        finishing.set()
        await release.wait()
        return await real_finish(*args)
    monkeypatch.setattr(service, "finish", held_finish)
    parent = asyncio.create_task(runner.run_worker({"prompt": "go"}, context))
    await asyncio.wait_for(finishing.wait(), 3)
    parent.cancel()
    await asyncio.sleep(0)
    parent.cancel()
    await asyncio.sleep(0)
    assert not parent.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(parent, 3)
    assert not service._live and runner._worker_count() == 0
    assert (await service.list(user_id=1))[0]["stored_status"] == "completed"


async def test_unadvertised_control_tool_is_refused_at_execution_boundary(harness):
    _, service, context, _, _ = harness
    from app.skills.legion import LegionControlSkill
    registry = CapabilityRegistry()
    await registry.register_skill(LegionControlSkill(service))
    calls = []

    class Client:
        async def create_message(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return SimpleNamespace(stop_reason="tool_use", usage=None, content=[SimpleNamespace(
                    type="tool_use", id="hidden-control", name="legion_control", input={"execution_id": "a" * 32, "action": "interrupt"})])
            assert "outside this worker's allowed tools" in str(kwargs["messages"][-1])
            return SimpleNamespace(stop_reason="end_turn", usage=None, content=[SimpleNamespace(type="text", text="done")])
    runner = LegionRunner(Client(), registry, None, worker_control=service)
    service.interrupt = AsyncMock(side_effect=AssertionError("Worker reached control surface"))
    assert await runner.run_worker({"prompt": "go", "legionnaire": "general"}, context) == "done"
    service.interrupt.assert_not_awaited()


async def test_partial_terminal_and_unsaved_progress_do_not_report_success(harness, monkeypatch):
    _, service, context, _, _ = harness
    runner = LegionRunner(object(), CapabilityRegistry(), None, worker_control=service)

    async def partial(**kwargs):
        await kwargs["emit"]({"phase": "finished", "ok": False, "report": "iteration cap: partial work"})
        return "iteration cap: partial work"
    runner._loop = partial
    with pytest.raises(RuntimeError, match="partial work"):
        await runner.run_worker({"prompt": "go"}, context)
    assert (await service.list(user_id=1))[0]["status"] == "failed"
    monkeypatch.setattr(service, "append", AsyncMock(side_effect=OSError("event commit failed")))
    runner = LegionRunner(object(), CapabilityRegistry(), None, worker_control=service)
    runner._forge = SimpleNamespace(run=AsyncMock())
    with pytest.raises(OSError, match="event commit"):
        await runner.run_worker({"prompt": "go", "legionnaire": "autobot"}, context)
    runner._forge.run.assert_not_awaited()
    assert (await service.list(user_id=1))[0]["status"] == "failed"


async def test_general_worker_parallel_reads_and_ordered_mutations(harness, monkeypatch):
    from app.skills.base import Skill
    _, service, context, _, _ = harness
    monkeypatch.setattr(settings, "legion_general_allow_mutating", True)
    timeline, active_reads = [], set()
    both_reads = asyncio.Event()

    class Read(Skill):
        name, description, input_schema, read_only = "control_test_read", "Read test data", {}, True
        async def execute(self, args, ctx):
            label = args["label"]
            active_reads.add(label)
            timeline.append(f"start:{label}")
            if len(active_reads) == 2:
                both_reads.set()
            if label in {"a", "b"}:
                await asyncio.wait_for(both_reads.wait(), 3)
            else:
                assert "write:first" in timeline
            await asyncio.sleep(0)
            active_reads.remove(label)
            timeline.append(f"end:{label}")
            return label
    class Write(Skill):
        name, description, input_schema = "control_test_write", "Write test data", {}
        async def execute(self, args, ctx):
            assert not active_reads
            timeline.append("write:" + args["label"])
            return args["label"]
    registry = CapabilityRegistry()
    await registry.register_skill(Read())
    await registry.register_skill(Write())
    calls = 0

    class Client:
        async def create_message(self, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                blocks = [SimpleNamespace(type="tool_use", id=str(i), name="control_test_" + kind, input={"label": label})
                          for i, (kind, label) in enumerate([("read", "a"), ("read", "b"), ("write", "first"), ("read", "c"), ("write", "last")])]
                return SimpleNamespace(content=blocks, stop_reason="tool_use", usage=None)
            assert [b["tool_use_id"] for b in kwargs["messages"][-1]["content"]] == ["0", "1", "2", "3", "4"]
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="done")], stop_reason="end_turn", usage=None)
    runner = LegionRunner(Client(), registry, None, worker_control=service)
    assert await runner.run_worker({"prompt": "go", "legionnaire": "general"}, context) == "done"
    assert timeline[:2] == ["start:a", "start:b"]
    assert timeline.index("write:first") > timeline.index("end:a")
    assert timeline.index("write:first") > timeline.index("end:b")
    assert timeline[-1] == "write:last"


async def test_saved_interrupt_retry_dispatches_missing_live_signal(harness):
    factory, service, context, _, _ = harness
    eid = await start(service, context)
    task, signal = await hold(service, eid)
    # The original HTTP coroutine could disappear after committing intent but
    # before signaling the local executor. A retry must complete that dispatch.
    async with factory() as db:
        await db.execute(update(WorkerExecution).where(WorkerExecution.id == eid).values(interrupt_requested=True))
        await db.commit()
    assert not signal.is_set()
    assert (await service.interrupt(eid, user_id=1))["accepted"]
    assert signal.is_set()
    assert (await service.interrupt(eid, user_id=1))["accepted"]
    await stop(service, eid, task)


async def test_hard_parent_cancel_retains_real_forge_claim_as_unknown(harness, monkeypatch):
    from forge.workshop import Workshop
    _, service, context, root, desk = harness
    monkeypatch.setattr(settings, "forge_workspace_root", str(root))
    monkeypatch.setattr(settings, "forge_cell_backend", "subprocess")
    entered, cancelled = asyncio.Event(), asyncio.Event()

    class Client:
        async def create_message(self, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
    runner = LegionRunner(Client(), CapabilityRegistry(), None, worker_control=service)
    parent = asyncio.create_task(runner.run_worker({"prompt": "review", "legionnaire": "forge_reviewer"}, context))
    await asyncio.wait_for(entered.wait(), 5)
    parent.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(parent, 5)
    assert cancelled.is_set() and not service._live
    saved = (await service.list(user_id=1))[0]
    assert saved["stored_status"] == "unknown"
    store = Workshop(root)
    assert store.run_status(saved["execution_id"])["status"] == "interrupted"
    with pytest.raises(ValueError, match="claimed"):
        store.claim(desk, "later", "do not enter an uncertain checkout")
