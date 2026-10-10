"""Crash windows in worker -> parent -> saved response -> push delivery.

Real WAL/FK SQLite, SessionManager, TurnRegistry and Legion control wrapper;
only provider responses, owner-memory post-turn tasks and Telegram are scripted.
"""
import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, event, select, update

from app.core import trigger_runner, turn_runner
from app.core.registry import CapabilityRegistry
from app.core.session_manager import SessionManager
from app.core.turn_runner import TurnRegistry
from app.legion.roster import LEGION_ROSTER
from app.legion.runner import LegionRunner
from app.models.message import Message
from app.models.session import Session
from app.models.worker_execution import WorkerCompletion, WorkerExecution
from app.schemas.sse import SSEEvent, SSEEventType
from app.services.completion_recovery import CompletionRecoveryService
from app.services.workspaces import WorkspaceError

from test_worker_control import harness  # shared isolated WAL/FK database fixture


class Profile:
    agent_id = "optimus"
    external_backend = False

    def allocate_model(self, _):
        return "test-model"

    def background_model(self, _):
        return "test-background"


@pytest.fixture
async def recovery(harness, monkeypatch):
    factory, control, context, root, desk = harness
    monkeypatch.setattr(turn_runner, "AsyncSessionLocal", factory)
    monkeypatch.setattr(trigger_runner, "AsyncSessionLocal", factory)
    from app.services import language, memory
    monkeypatch.setattr(memory, "run_post_turn_tasks", AsyncMock())
    monkeypatch.setattr(language, "enforce", AsyncMock(side_effect=lambda text, *a, **kw: text))
    sm = SessionManager()
    turns = TurnRegistry(sm)
    bots = SimpleNamespace(deliver_message=AsyncMock(return_value=True))
    calls = []

    async def engine(ctx):
        calls.append(ctx)
        yield SSEEvent(SSEEventType.CHUNK, f"Verified worker report {ctx.request_id}", ctx.session_id, ctx.request_id)
        yield SSEEvent(SSEEventType.DONE, {}, ctx.session_id, ctx.request_id)

    deps = dict(profiles={"optimus": Profile()}, orchestrator=SimpleNamespace(run=engine), turns=turns,
        session_manager=sm, telegram_bots=bots, agent_proxy=None, ws_manager=None,
        workspace_service=None, input_service=None)
    service = CompletionRecoveryService(factory)
    service.wire(**deps)
    turns.set_settled_hook(service.drain)
    state = SimpleNamespace(factory=factory, control=control, context=context, root=root, desk=desk,
        sm=sm, turns=turns, bots=bots, calls=calls, service=service, deps=deps)
    yield state
    service.close()
    turns.set_settled_hook(None)
    await turns.shutdown()


async def finished(state, *, backend="forge", status="completed", background=True):
    eid = uuid.uuid4().hex
    await state.control.begin(eid, context=state.context,
        worker=LEGION_ROSTER["autobot" if backend == "forge" else "scout"], model="test",
        task="write two modules and verify them", ui_run_id="call", background=background, ticket_id=77)
    await state.control.finish(eid, status, "Two modules verified; no claim needs reconciliation.")
    return eid


async def receipt(state, eid):
    async with state.factory() as db:
        return await db.get(WorkerCompletion, eid)


async def messages(state):
    async with state.factory() as db:
        return (await db.execute(select(Message).order_by(Message.id))).scalars().all()


async def settle_turns(state):
    async def settle():
        # The chat slot releases before its task finishes the completion drain.
        # Await owned tasks too; active() is intentionally only engine admission.
        while owned := [turn.request_id for turn in state.turns._turns.values()
                        if turn.task is not None and not turn.task.done()]:
            for request_id in owned:
                await state.turns.wait(request_id, timeout=10)
    await asyncio.wait_for(settle(), 20)


def rebuilt(state):
    service = CompletionRecoveryService(state.factory)
    service.wire(**state.deps)
    state.turns.set_settled_hook(service.drain)
    return service


async def test_terminal_and_receipt_are_atomic_and_inline_has_no_outbox(recovery):
    s = recovery
    inline = await finished(s, background=False)
    assert await receipt(s, inline) is None
    eid = uuid.uuid4().hex
    await s.control.begin(eid, context=s.context, worker=LEGION_ROSTER["autobot"], model="test",
                          task="go", ui_run_id="call", background=True)

    def fail_insert(*_):
        raise OSError("outbox unavailable")
    event.listen(WorkerCompletion, "before_insert", fail_insert)
    try:
        with pytest.raises(OSError, match="outbox"):
            await s.control.finish(eid, "completed", "success")
    finally:
        event.remove(WorkerCompletion, "before_insert", fail_insert)
    saved = await s.control.inspect(eid, user_id=1)
    assert saved["stored_status"] == "running" and saved["last_sequence"] == 1
    assert saved["completion"] is None
    await s.control.finish(eid, "completed", "success")
    saved = await s.control.inspect(eid, user_id=1)
    assert saved["stored_status"] == "completed" and saved["completion"]["status"] == "pending"


async def test_busy_parent_has_no_seed_until_it_releases_its_turn(recovery):
    s = recovery
    entered, release = asyncio.Event(), asyncio.Event()

    async def parent(ctx):
        entered.set()
        await release.wait()
        yield SSEEvent(SSEEventType.CHUNK, "Parent acknowledged the assignment", ctx.session_id, ctx.request_id)
        yield SSEEvent(SSEEventType.DONE, {}, ctx.session_id, ctx.request_id)
    s.turns.start(context=s.context, engine_factory=parent, format_error=str)
    await entered.wait()
    eid = await finished(s)
    hook = trigger_runner.make_legion_reporter(**s.deps, completion_service=s.service)
    await asyncio.wait_for(hook(agent_id="optimus", worker_id="autobot", task="go", result="done",
        status="ok", execution_id=eid, room_session_id=s.context.session_id), 5)
    assert (await receipt(s, eid)).status == "pending"
    assert not await messages(s) and not s.calls
    release.set()
    await settle_turns(s)
    row = await receipt(s, eid)
    assert row.status == "completed" and row.delivery_status == "delivered"
    assert len(s.calls) == 1 and s.calls[0].session_id == s.context.session_id
    assert [m.role for m in await messages(s)] == ["assistant", "user", "assistant"]
    s.bots.deliver_message.assert_awaited_once()


async def test_restart_and_repeated_drains_deliver_once_without_worker_reexecution(recovery):
    s = recovery
    eid = await finished(s)
    service = rebuilt(s)
    assert (await service.recover_on_startup())["started"] == 1
    await settle_turns(s)
    await asyncio.gather(*[service.drain() for _ in range(5)])
    await rebuilt(s).recover_on_startup()
    assert len(s.calls) == 1 and len(await messages(s)) == 2
    assert (await receipt(s, eid)).attempts == 1
    s.bots.deliver_message.assert_awaited_once()
    assert (await s.control.inspect(eid, user_id=1))["stored_status"] == "completed"


async def test_preparation_failure_reuses_committed_seed_and_releases_admission(recovery, monkeypatch):
    s = recovery
    eid = await finished(s)
    original = s.sm.load_history
    monkeypatch.setattr(s.sm, "load_history", AsyncMock(side_effect=OSError("history temporarily unavailable")))
    assert (await s.service.drain())["errors"] == 1
    row = await receipt(s, eid)
    seed_id = row.seed_message_id
    assert row.status == "pending" and seed_id and len(await messages(s)) == 1
    assert not s.turns.active() and not s.calls
    monkeypatch.setattr(s.sm, "load_history", original)
    await rebuilt(s).recover_on_startup()
    await settle_turns(s)
    row = await receipt(s, eid)
    assert row.seed_message_id == seed_id and row.attempts == 2
    assert len(await messages(s)) == 2 and len(s.calls) == 1


async def test_restart_before_provider_start_reuses_seed(recovery):
    s = recovery
    eid = await finished(s)
    async with s.factory() as db:
        await s.service.save_seed(db, eid, s.sm, s.context.session_id, [{"type": "text", "text": "queued report"}])
    seed_id = (await receipt(s, eid)).seed_message_id
    assert (await receipt(s, eid)).status == "ready"
    await rebuilt(s).recover_on_startup()
    await settle_turns(s)
    assert (await receipt(s, eid)).seed_message_id == seed_id
    assert len(s.calls) == 1 and len(await messages(s)) == 2


async def test_started_report_without_terminal_proof_is_unknown_and_never_replayed(recovery):
    s = recovery
    eid = await finished(s)
    async with s.factory() as db:
        await s.service.save_seed(db, eid, s.sm, s.context.session_id, [{"type": "text", "text": "report"}])
    effects = []

    async def partial(ctx):
        effects.append("side effect could already have happened")
        yield SSEEvent(SSEEventType.TOOL, {"name": "write"}, ctx.session_id, ctx.request_id)
    source = s.service.run_report(eid, s.context, partial)
    await anext(source)
    await source.aclose()  # lost executor; no TurnRegistry terminal save
    assert (await receipt(s, eid)).status == "running"
    service = rebuilt(s)
    await service.recover_on_startup()
    await service.drain()
    row = await receipt(s, eid)
    assert row.status == "unknown" and "disappeared" in row.last_error
    assert len(effects) == 1 and not s.calls
    s.bots.deliver_message.assert_not_awaited()
    assert (await s.control.inspect(eid, user_id=1))["stored_status"] == "completed"


async def test_saved_response_recovers_lost_ack_and_pushes_exact_report_not_latest_reply(recovery, monkeypatch):
    s = recovery
    eid = await finished(s)
    s.turns.set_settled_hook(None)
    monkeypatch.setattr(s.service, "settle", AsyncMock(side_effect=OSError("ack lost")))
    await s.service.drain()
    await settle_turns(s)
    assert (await receipt(s, eid)).status == "running"
    async with s.factory() as db:
        await s.sm.save_message(db, s.context.session_id, "assistant", [
            {"type": "text", "text": "Newer, unrelated reply"},
            {"type": "_speda_meta", "turn": {"request_id": "newer", "status": "completed"}}])
    service = rebuilt(s)
    await service.recover_on_startup()
    row = await receipt(s, eid)
    assert row.status == "completed" and row.delivery_status == "delivered"
    assert s.bots.deliver_message.await_args.args[1] == f"Verified worker report {row.request_id}"
    assert len(s.calls) == 1 and len(await messages(s)) == 3


async def test_push_failure_retries_delivery_without_another_model_turn(recovery):
    s = recovery
    eid = await finished(s)
    s.bots.deliver_message.side_effect = [OSError("network unavailable"), True]
    await s.service.drain()
    await settle_turns(s)
    row = await receipt(s, eid)
    assert row.status == "completed" and row.delivery_status == "pending"
    assert row.delivery_attempts == 1 and "network unavailable" in row.last_error
    await rebuilt(s).recover_on_startup()
    await s.service.drain()
    row = await receipt(s, eid)
    assert row.delivery_status == "delivered" and row.delivery_attempts == 2
    assert len(s.calls) == 1 and len(await messages(s)) == 2
    assert s.bots.deliver_message.await_count == 2


@pytest.mark.parametrize("origin", ["deleted", "closed", "owner", "agent"])
async def test_invalid_origin_is_retained_and_never_redirected_to_a_new_chat(recovery, origin):
    s = recovery
    eid = await finished(s)
    async with s.factory() as db:
        if origin == "deleted":
            await db.execute(delete(Session).where(Session.id == s.context.session_id))
        else:
            from datetime import datetime, timezone
            values = {"closed": {"ended_at": datetime.now(timezone.utc).replace(tzinfo=None)},
                      "owner": {"user_id": 2}, "agent": {"agent_id": "sentinel"}}[origin]
            await db.execute(update(Session).where(Session.id == s.context.session_id).values(**values))
        await db.commit()
    assert (await s.service.drain())["errors"] == 1
    assert (await receipt(s, eid)).status == "blocked"
    assert not await messages(s) and not s.calls
    assert (await s.control.inspect(eid, user_id=1))["completion"]["status"] == "blocked"
    async with s.factory() as db:
        assert len((await db.execute(select(Session))).scalars().all()) == (0 if origin == "deleted" else 1)


async def test_capacity_defers_seed_and_worker_report_without_losing_receipt(recovery):
    s = recovery
    release = asyncio.Event()

    async def hold(ctx):
        await release.wait()
        yield SSEEvent(SSEEventType.DONE, {}, ctx.session_id, ctx.request_id)
    from dataclasses import replace
    async with s.factory() as db:
        for sid in range(100, 108):
            db.add(Session(id=sid, user_id=1, agent_id="optimus", model_used="test", triggered_by="user"))
        await db.commit()
    for sid in range(100, 108):
        ctx = replace(s.context, session_id=sid, request_id=f"busy-{sid}", extra={})
        assert s.turns.start(context=ctx, engine_factory=hold, format_error=str)
    eid = await finished(s)
    assert (await s.service.drain())["deferred"] == 1
    assert (await receipt(s, eid)).status == "pending" and not await messages(s)
    release.set()
    await settle_turns(s)
    assert (await receipt(s, eid)).status == "completed" and len(s.calls) == 1


@pytest.mark.parametrize("worker_count", [2, 4, 8])
async def test_multiple_completions_serialize_in_one_parent_with_stable_ids(recovery, worker_count):
    s = recovery
    ids = [await finished(s) for _ in range(worker_count)]
    await asyncio.gather(*[s.service.drain() for _ in range(4)])
    await settle_turns(s)
    rows = [await receipt(s, eid) for eid in ids]
    assert all(row.status == "completed" and row.attempts == 1 for row in rows), [
        (row.execution_id, row.status, row.attempts, row.delivery_status, row.last_error) for row in rows]
    assert len(s.calls) == worker_count and len({ctx.request_id for ctx in s.calls}) == worker_count
    assert [m.role for m in await messages(s)] == ["user", "assistant"] * worker_count
    assert s.bots.deliver_message.await_count == worker_count


async def test_legion_completion_survives_failed_report_callback_and_covers_general_workers(recovery):
    s = recovery
    runner = LegionRunner(object(), CapabilityRegistry(), None, worker_control=s.control)
    runner._log_start = AsyncMock(return_value=77)
    runner._log_finish = AsyncMock()
    runner._loop = AsyncMock(return_value="actual saved result")
    runner.set_report_hook(AsyncMock(side_effect=OSError("callback lost")))
    reply = await runner.run_worker({"legionnaire": "scout", "prompt": "check", "run_in_background": True}, s.context)
    assert "execution" in reply
    await asyncio.gather(*list(runner._background))
    saved = (await s.control.list(user_id=1))[0]
    eid = saved["execution_id"]
    assert saved["completion"]["status"] == "pending"
    assert runner._report_hook.await_args.kwargs["execution_id"] == eid
    await rebuilt(s).recover_on_startup()
    await settle_turns(s)
    assert (await receipt(s, eid)).status == "completed"
    seed = "\n".join(block.get("text", "") for block in s.calls[0].conversation_history[-1]["content"])
    assert "BACKGROUND WORK COMPLETE" in seed and "engineering milestone" not in seed
    runner._loop.assert_awaited_once()


async def test_failed_report_persists_error_and_never_reruns_provider(recovery):
    s = recovery
    eid = await finished(s)
    effects = []

    async def error(ctx):
        effects.append("tool may have run")
        raise OSError("provider failed after tool")
        yield
    s.deps["orchestrator"].run = error
    await s.service.drain()
    await settle_turns(s)
    assert (await receipt(s, eid)).status == "failed"
    assert "provider failed" in s.bots.deliver_message.await_args.args[1]
    await rebuilt(s).recover_on_startup()
    await s.service.drain()
    assert len(effects) == 1 and len(await messages(s)) == 2


async def test_trigger_refusal_and_preparation_cancel_release_slot_before_history(recovery, monkeypatch):
    s = recovery
    eid = await finished(s)
    entered, release = asyncio.Event(), asyncio.Event()
    original = s.sm.load_history

    async def blocked_load(*args):
        entered.set()
        await release.wait()
        return await original(*args)
    monkeypatch.setattr(s.sm, "load_history", blocked_load)
    drain = asyncio.create_task(s.service.drain())
    await entered.wait()
    assert len(s.turns.active()) == 1 and not s.calls
    async with s.factory() as db:
        started, _ = await trigger_runner.start_trigger_turn(db=db, profile=Profile(), payload={"type": "probe"},
            output_mode="silent", request_id="refused", orchestrator=s.deps["orchestrator"], turns=s.turns,
            session_manager=s.sm, telegram_bots=s.bots, session_id=s.context.session_id)
    assert started is None and len(await messages(s)) == 1
    drain.cancel()
    await asyncio.gather(drain, return_exceptions=True)
    assert not s.turns.active() and (await receipt(s, eid)).status == "ready"
    monkeypatch.setattr(s.sm, "load_history", original)
    await s.service.drain()
    await settle_turns(s)
    assert len(s.calls) == 1 and len(await messages(s)) == 2


async def test_unknown_worker_outcome_is_reported_without_releasing_workshop_claim(recovery):
    from forge.workshop import Workshop
    s = recovery
    eid = await finished(s, status="unknown")
    store = Workshop(s.root)
    store.claim(s.desk, eid, "uncertain cleanup")
    store.interrupt(eid, "unconfirmed removal")
    await s.service.drain()
    await settle_turns(s)
    assert (await receipt(s, eid)).status == "completed"
    seed = "\n".join(block.get("text", "") for block in s.calls[0].conversation_history[-1]["content"])
    assert "OUTCOME UNCONFIRMED" in seed and "ALREADY DONE" not in seed
    assert store.run_status(eid)["status"] == "interrupted"
    with pytest.raises(ValueError, match="claimed"):
        store.claim(s.desk, "replacement", "new work")


async def test_completion_receipt_is_in_raw_export_and_owner_scoped_inspection(recovery):
    from app.services.chat_history import debug_export
    s = recovery
    eid = await finished(s)
    async with s.factory() as db:
        exported = await debug_export(db, s.context.session_id)
    assert exported["worker_completions"][0]["execution_id"] == eid
    assert exported["worker_completions"][0]["status"] == "pending"
    with pytest.raises(WorkspaceError) as denied:
        await s.control.inspect(eid, user_id=2)
    assert denied.value.status_code == 404


async def test_bounded_drain_rotates_busy_receipts_so_other_sessions_can_progress(recovery):
    from dataclasses import replace
    s = recovery
    slot = s.turns.reserve(request_id="parent-reservation", agent_id="optimus", session_id=s.context.session_id)
    first = await finished(s)
    async with s.factory() as db:
        db.add(Session(id=101, user_id=1, agent_id="optimus", model_used="test", triggered_by="user"))
        await db.commit()
    original = s.context
    s.context = replace(original, session_id=101, request_id="other-parent", extra={})
    second = await finished(s)
    s.context = original
    assert (await s.service.drain(limit=1))["deferred"] == 1
    assert (await s.service.drain(limit=1))["started"] == 1
    await settle_turns_for(s, f"worker-report-{second}")
    assert (await receipt(s, first)).status == "pending"
    s.turns.release(slot)
    await s.service.drain()
    await settle_turns(s)
    assert (await receipt(s, first)).status == "completed"


async def settle_turns_for(state, request_id):
    assert await state.turns.wait(request_id, timeout=10)


async def test_notification_storage_failure_is_pending_and_retries_atomically(recovery):
    from app.models.notification import Notification
    s = recovery
    eid = await finished(s)
    s.bots.deliver_message.return_value = False

    def fail_insert(*_):
        raise OSError("notification disk full")
    event.listen(Notification, "before_insert", fail_insert)
    try:
        await s.service.drain()
        await settle_turns(s)
    finally:
        event.remove(Notification, "before_insert", fail_insert)
    row = await receipt(s, eid)
    assert row.status == "completed" and row.delivery_status == "pending"
    assert "disk full" in row.last_error
    async with s.factory() as db:
        assert not (await db.execute(select(Notification))).scalars().all()
    await s.service.drain()
    await rebuilt(s).recover_on_startup()
    async with s.factory() as db:
        notifications = (await db.execute(select(Notification))).scalars().all()
    assert len(notifications) == 1 and notifications[0].triggered_by == "agent"
    assert (await receipt(s, eid)).delivery_status == "delivered" and len(s.calls) == 1


async def test_lost_ack_after_notification_commit_does_not_insert_another_notification(recovery, monkeypatch):
    from app.models.notification import Notification
    s = recovery
    eid = await finished(s)
    s.bots.deliver_message.return_value = False
    original = trigger_runner._store_notification

    async def lost_ack(*args, **kwargs):
        await original(*args, **kwargs)
        raise OSError("lost notification acknowledgement")
    monkeypatch.setattr(trigger_runner, "_store_notification", lost_ack)
    await s.service.drain()
    await settle_turns(s)
    assert (await receipt(s, eid)).delivery_status == "delivered"
    await rebuilt(s).recover_on_startup()
    await s.service.drain()
    async with s.factory() as db:
        assert len((await db.execute(select(Notification))).scalars().all()) == 1
    assert len(s.calls) == 1


async def test_cancelled_delivery_joins_receipt_cleanup_and_next_drain_retries(recovery, monkeypatch):
    s = recovery
    eid = await finished(s)
    s.turns.set_settled_hook(None)
    await s.service.drain()
    await settle_turns(s)
    entered, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def blocked_push(*_):
        entered.set()
        await asyncio.Event().wait()

    original = s.service._delivery_interrupted

    async def held_cleanup(eid):
        cleanup.set()
        await release.wait()
        await original(eid)
    monkeypatch.setattr(s.service, "_delivery_interrupted", held_cleanup)
    s.bots.deliver_message.side_effect = blocked_push
    drain = asyncio.create_task(s.service.drain())
    await asyncio.wait_for(entered.wait(), 5)
    assert (await receipt(s, eid)).delivery_status == "sending"
    drain.cancel()
    await asyncio.wait_for(cleanup.wait(), 5)
    drain.cancel()
    drain.cancel()
    assert not drain.done()
    release.set()
    result = await asyncio.wait_for(asyncio.gather(drain, return_exceptions=True), 5)
    assert isinstance(result[0], asyncio.CancelledError)
    assert (await receipt(s, eid)).delivery_status == "pending"
    s.bots.deliver_message.side_effect = None
    await s.service.drain()
    assert (await receipt(s, eid)).delivery_status == "delivered"
    assert len(s.calls) == 1


async def test_missing_report_history_is_unknown_and_does_not_push_an_older_answer(recovery, monkeypatch):
    s = recovery
    eid = await finished(s)
    original = s.sm.save_message
    async with s.factory() as db:
        await original(db, s.context.session_id, "assistant", "Old answer")

    async def failing_save(db, sid, role, content, **kwargs):
        if role == "assistant":
            raise OSError("report history disk full")
        return await original(db, sid, role, content, **kwargs)
    monkeypatch.setattr(s.sm, "save_message", failing_save)
    await s.service.drain()
    await settle_turns(s)
    assert (await receipt(s, eid)).status == "unknown"
    s.bots.deliver_message.assert_not_awaited()
    await rebuilt(s).recover_on_startup()
    assert len(s.calls) == 1 and len(await messages(s)) == 2


async def test_empty_report_sends_saved_result_instead_of_acknowledging_no_delivery(recovery):
    s = recovery
    eid = await finished(s)

    async def empty(ctx):
        yield SSEEvent(SSEEventType.DONE, {}, ctx.session_id, ctx.request_id)
    s.deps["orchestrator"].run = empty
    await s.service.drain()
    await settle_turns(s)
    assert "Two modules verified" in s.bots.deliver_message.await_args.args[1]
    assert (await receipt(s, eid)).delivery_status == "delivered"


async def test_recovered_report_restores_bound_desk_project_and_original_inputs(recovery):
    import base64
    from app.models.project import Project
    from app.services.engineering_inputs import EngineeringInputService
    from app.services.workspaces import WorkspaceService
    s = recovery
    workspaces = WorkspaceService(str(s.root))
    inputs = EngineeringInputService(str(s.root), workspaces)
    async with s.factory() as db:
        project = Project(user_id=1, agent_id="optimus", name="Private task context")
        db.add(project)
        await db.flush()
        session = await db.get(Session, s.context.session_id)
        session.project_id = project.id
        await db.commit()
        s.context.db = db
        chosen = await workspaces.prepare(s.context, workspace=str(s.desk))
        await inputs.accept(s.context, [{"name": "spec.md", "data": base64.b64encode(b"original requirements").decode()}])
        project_id = project.id
    eid = await finished(s)
    other = s.root / "other"
    other.mkdir()
    from forge.workshop import Workshop
    Workshop(s.root).register(other)
    s.deps["workspace_service"] = WorkspaceService(str(s.root))
    s.deps["input_service"] = EngineeringInputService(str(s.root), s.deps["workspace_service"])
    await rebuilt(s).recover_on_startup()
    await settle_turns(s)
    captured = s.calls[0]
    assert captured.workshop_project_id == chosen.project_id
    assert captured.extra["cwd"] == str(s.desk.resolve())
    assert captured.extra["project_id"] == project_id
    assert captured.extra["forge_inputs"][0]["name"] == "spec.md"
    assert (await receipt(s, eid)).status == "completed"


async def test_existing_authenticated_n8n_task_drain_recovers_completion(recovery, monkeypatch):
    import httpx
    from fastapi import FastAPI
    from app.config import settings
    from app.middleware.auth import AuthMiddleware
    from app.routers.admin import router
    from app.services import task_queue
    s = recovery
    eid = await finished(s)
    monkeypatch.setattr(settings, "speda_api_key", "test-completion-key")
    jobs = AsyncMock(return_value={"done": 0, "failed": 0})
    monkeypatch.setattr(task_queue, "drain", jobs)
    monkeypatch.setattr(task_queue, "queue_stats", AsyncMock(return_value={}))
    monkeypatch.setattr(task_queue, "reclaim_stale", AsyncMock(return_value=0))
    app = FastAPI()
    app.state.completion_recovery = s.service
    app.include_router(router)
    app.add_middleware(AuthMiddleware)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/admin/tasks/drain")).status_code == 401
        jobs.assert_not_awaited()
        assert (await receipt(s, eid)).status == "pending"
        response = await client.post("/admin/tasks/drain", headers={"X-API-Key": "test-completion-key"})
        assert response.status_code == 200 and response.json()["completions"]["started"] == 1
    await settle_turns(s)
    assert (await receipt(s, eid)).delivery_status == "delivered" and len(s.calls) == 1
