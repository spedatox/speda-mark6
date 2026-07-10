"""Unit tests for the detached TurnRegistry (BgOps Phase 1)."""

import asyncio

import pytest

from app.core import turn_runner
from app.core.context import AgentContext
from app.schemas.sse import SSEEvent, SSEEventType


class _FakeSM:
    def __init__(self):
        self.saved = []

    async def save_message(self, db, sid, role, content):
        self.saved.append((sid, role, content))


class _DummyCtx:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *a):
        return False


@pytest.fixture(autouse=True)
def _no_db(monkeypatch):
    # The runner opens its own AsyncSessionLocal — stub it (these tests never
    # touch a real DB; persistence is verified via the fake session manager).
    monkeypatch.setattr(turn_runner, "AsyncSessionLocal", lambda: _DummyCtx())


def _ctx(rid):
    return AgentContext(
        agent_id="speda", user_id=1, session_id=7, request_id=rid,
        triggered_by="user", trigger_payload={}, output_mode="respond", model="m",
        system_prompt="", conversation_history=[], db=None, timezone="UTC",
    )


async def _slow_engine(ctx):
    for i in range(5):
        yield SSEEvent(SSEEventType.CHUNK, f"part{i} ", ctx.session_id, ctx.request_id)
        await asyncio.sleep(0.02)
    yield SSEEvent(SSEEventType.DONE, "done", ctx.session_id, ctx.request_id)


async def _collect(reg, rid):
    return [s async for s in reg.subscribe(rid)]


async def test_disconnect_does_not_cancel_and_persists():
    sm = _FakeSM()
    reg = turn_runner.TurnRegistry(sm)
    completed = []
    reg.start(context=_ctx("r1"), engine_factory=_slow_engine,
              format_error=str, on_complete=lambda: _set(completed))
    got = []
    async for sse in reg.subscribe("r1"):
        got.append(sse)
        if len(got) == 2:
            break  # simulate a dropped connection
    await asyncio.sleep(0.4)
    assert len(sm.saved) == 1
    assert sm.saved[0][2][0]["text"] == "part0 part1 part2 part3 part4 "
    assert completed == [True]


async def test_reattach_replays_full_stream():
    sm = _FakeSM()
    reg = turn_runner.TurnRegistry(sm)
    reg.start(context=_ctx("r2"), engine_factory=_slow_engine, format_error=str, on_complete=None)
    await asyncio.sleep(0.4)
    replay = await _collect(reg, "r2")
    assert len(replay) == 6  # 5 chunks + done


async def test_cancel_persists_partial_with_marker():
    sm = _FakeSM()
    reg = turn_runner.TurnRegistry(sm)
    reg.start(context=_ctx("r3"), engine_factory=_slow_engine, format_error=str, on_complete=None)
    await asyncio.sleep(0.05)
    assert await reg.cancel("r3") is True
    await asyncio.sleep(0.15)
    assert "[cancelled by owner]" in sm.saved[0][2][0]["text"]


async def test_two_subscribers_identical():
    sm = _FakeSM()
    reg = turn_runner.TurnRegistry(sm)
    reg.start(context=_ctx("r4"), engine_factory=_slow_engine, format_error=str, on_complete=None)
    a, b = await asyncio.gather(_collect(reg, "r4"), _collect(reg, "r4"))
    assert a == b and len(a) == 6


async def test_active_and_cap():
    sm = _FakeSM()
    reg = turn_runner.TurnRegistry(sm)
    reg.start(context=_ctx("r5"), engine_factory=_slow_engine, format_error=str, on_complete=None)
    act = reg.active(session_id=7)
    assert len(act) == 1 and act[0]["request_id"] == "r5"
    await asyncio.sleep(0.4)
    assert reg.active() == []  # finished turns are not "active"


def _set(lst):
    async def _f():
        lst.append(True)
    return _f()
