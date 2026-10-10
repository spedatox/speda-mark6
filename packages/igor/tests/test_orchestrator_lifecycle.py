"""Fault regressions for Codex-referenced turn and dispatch ownership."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import settings
from app.core.context import AgentContext
from app.core.orchestrator import AgentOrchestrator, _tool_batch
from app.core.turn_runner import TurnRegistry
from app.schemas.sse import SSEEventType
from app.services.llm_client import TextBlock, ToolUseBlock


def _context():
    return AgentContext(agent_id="fixture", user_id=1, session_id=1, request_id="fixture",
                        triggered_by="user", trigger_payload={}, output_mode="respond", model="fixture",
                        system_prompt="", conversation_history=[{"role": "user", "content": "Do the work"}], db=None)


def _block(name):
    return SimpleNamespace(id=name, name=name, input={})


async def test_tool_batch_waits_for_cancelled_child_cleanup():
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class Registry:
        def call_is_read_only(self, *args):
            return True

        async def execute(self, *args, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()

    task = asyncio.create_task(_tool_batch([_block("held")], _context(), Registry(), lambda _: None))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    await asyncio.wait_for(cleaning.wait(), 5)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)


async def test_reads_overlap_but_mutation_waits_and_later_read_observes_it():
    reading, release = asyncio.Event(), asyncio.Event()
    active_reads, state, observed = 0, 0, []

    class Registry:
        def call_is_read_only(self, name, args):
            return name != "write"

        async def execute(self, name, args, context, **kwargs):
            nonlocal active_reads, state
            if name in {"a", "b"}:
                active_reads += 1
                if active_reads == 2:
                    reading.set()
                await release.wait()
                active_reads -= 1
            elif name == "write":
                assert active_reads == 0
                state = 1
            else:
                observed.append(state)
            return name

    blocks = [_block(name) for name in ["a", "b", "write", "after"]]
    task = asyncio.create_task(_tool_batch(blocks, _context(), Registry(), lambda _: None))
    await asyncio.wait_for(reading.wait(), 5)
    assert state == 0
    release.set()
    results = await asyncio.wait_for(task, 5)
    assert [result for result, duration in results] == ["a", "b", "write", "after"]
    assert observed == [1]


def _engine(responses, monkeypatch):
    monkeypatch.setattr("app.core.runtime_state.get_house_party", lambda: False)
    monkeypatch.setattr("app.core.runtime_state.get_budget_mode", lambda: False)
    profile = MagicMock()
    profile.tool_allowlist = None
    profile.build_system_prompt.return_value = "Fixture system"
    profile.personalization_prompt.return_value = ""
    registry = MagicMock()
    registry.dead_zone_active = AsyncMock(return_value=False)
    registry.tool_index.return_value = ""
    registry.list_tools.return_value = []
    registry.call_is_read_only.return_value = True
    registry.execute = AsyncMock(return_value="done")
    profiles = MagicMock()
    profiles.require.return_value = profile
    calls = []

    class Stream:
        def __init__(self, response):
            self.response = response

        @property
        def text_stream(self):
            async def chunks():
                yield "partial"
            return chunks()

        async def get_final_message(self):
            return self.response

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class Client:
        def stream_message(self, **kwargs):
            calls.append(deepcopy(kwargs))
            return Stream(responses.pop(0))

    engine = AgentOrchestrator(registry, Client(), profiles, None)
    return engine, calls, registry


@pytest.mark.parametrize("reason", [None, "unknown"])
async def test_unknown_stop_reason_cannot_emit_done(reason, monkeypatch):
    engine, calls, registry = _engine([SimpleNamespace(content=[TextBlock("partial")], stop_reason=reason)], monkeypatch)
    events = [e async for e in engine.run(_context())]
    assert events[-1].type == SSEEventType.ERROR
    assert not any(e.type == SSEEventType.DONE for e in events)


@pytest.mark.parametrize("reason", ["max_tokens", "pause_turn"])
async def test_repeated_model_continuations_have_a_separate_safety_guard(reason, monkeypatch):
    monkeypatch.setattr(settings, "chat_max_continuations", 2)
    monkeypatch.setattr(settings, "chat_max_output_tokens", 100)
    monkeypatch.setattr(settings, "chat_recovery_max_output_tokens", 250)
    responses = [SimpleNamespace(content=[TextBlock("partial")], stop_reason=reason) for _ in range(3)]
    engine, calls, registry = _engine(responses, monkeypatch)
    events = [e async for e in engine.run(_context())]
    assert events[-1].type == SSEEventType.ERROR and "recovery was exhausted" in events[-1].data
    assert len(calls) == 3
    assert [call["max_tokens"] for call in calls] == ([100, 200, 250] if reason == "max_tokens" else [100] * 3)


@pytest.mark.parametrize("reason", ["max_tokens", "pause_turn"])
async def test_unfinished_tool_request_is_not_executed_and_has_a_legal_result(monkeypatch, reason):
    engine, calls, registry = _engine([
        SimpleNamespace(content=[ToolUseBlock(id="partial-call", name="write", input={})], stop_reason=reason),
        SimpleNamespace(content=[TextBlock("Done")], stop_reason="end_turn"),
    ], monkeypatch)
    events = [e async for e in engine.run(_context())]
    assert events[-1].type == SSEEventType.DONE
    registry.execute.assert_not_awaited()
    results = [block for message in calls[1]["messages"] if isinstance(message["content"], list)
               for block in message["content"] if block.get("type") == "tool_result"]
    assert results[0]["tool_use_id"] == "partial-call" and results[0]["is_error"]


async def test_orchestrator_cancellation_collects_progress_waiter_and_tool(monkeypatch):
    engine, calls, registry = _engine([
        SimpleNamespace(content=[ToolUseBlock(id="held", name="Task", input={})], stop_reason="tool_use"),
    ], monkeypatch)
    started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def execute(*args, **kwargs):
        kwargs["emit"]({"id": "held", "phase": "started"})
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()

    registry.execute = execute
    events = []

    async def consume():
        async for event in engine.run(_context()):
            events.append(event)

    prior_tasks = asyncio.all_tasks()
    parent = asyncio.create_task(consume())
    await asyncio.wait_for(started.wait(), 5)
    parent.cancel()
    await asyncio.wait_for(cleaning.wait(), 5)
    assert not parent.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(parent, 5)
    assert not (asyncio.all_tasks() - prior_tasks), "dispatch or progress waiter outlived the parent"


async def test_known_http_request_id_is_rejected_before_history_or_execution():
    from fastapi import BackgroundTasks, HTTPException
    from app.routers.chat import _run_chat
    registry = TurnRegistry(MagicMock())
    registry._turns["existing"] = object()  # known, without launching a DB-backed task
    sessions = MagicMock()
    profiles = MagicMock()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        turns=registry, session_manager=sessions, profiles=profiles, orchestrator=MagicMock(),
    )))
    with pytest.raises(HTTPException) as error:
        await _run_chat(request, "fixture", SimpleNamespace(request_id="existing"), BackgroundTasks(), None)
    assert error.value.status_code == 409
    sessions.save_message.assert_not_called()
    sessions.get_or_create.assert_not_called()
