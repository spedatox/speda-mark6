# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Unit tests for the detached TurnRegistry (BgOps Phase 1)."""

import asyncio

import pytest

from app.core import turn_runner
from app.core.context import AgentContext
from app.schemas.sse import SSEEvent, SSEEventType


class _FakeSM:
    def __init__(self):
        self.saved = []
        self.usage = []

    async def save_message(self, db, sid, role, content):
        self.saved.append((sid, role, content))

    async def add_token_usage(self, db, sid, tin, tout):
        self.usage.append((sid, tin, tout))


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


async def _graceful_error_engine(ctx):
    yield SSEEvent(SSEEventType.CHUNK, "working ", ctx.session_id, ctx.request_id)
    yield SSEEvent(SSEEventType.ERROR, "boom", ctx.session_id, ctx.request_id)


async def _raise_engine(ctx):
    yield SSEEvent(SSEEventType.CHUNK, "did stuff ", ctx.session_id, ctx.request_id)
    raise RuntimeError("kaboom")


async def _tool_only_engine(ctx):
    yield SSEEvent(SSEEventType.TOOL, {"id": "t1", "name": "run", "input": {}},
                   ctx.session_id, ctx.request_id)
    yield SSEEvent(SSEEventType.DONE, "", ctx.session_id, ctx.request_id)


async def test_graceful_error_persists_partial_with_marker_and_skips_on_complete():
    """A peer/engine ERROR event must not erase the turn — the streamed text
    survives with a failure marker, and post-turn work is skipped."""
    sm = _FakeSM()
    reg = turn_runner.TurnRegistry(sm)
    completed = []
    reg.start(context=_ctx("e1"), engine_factory=_graceful_error_engine,
              format_error=str, on_complete=lambda: _set(completed))
    await asyncio.sleep(0.2)
    assert len(sm.saved) == 1
    text = sm.saved[0][2][0]["text"]
    assert "working " in text and "error: boom" in text
    assert completed == []              # a failed turn is not titled/embedded


async def _spending_engine(ctx):
    """An engine that bills tokens across two loop iterations, as a tool-using
    turn does — the orchestrator accumulates into ctx.extra as it goes."""
    ctx.extra["token_usage"] = {"input": 0, "output": 0}
    for i in range(2):
        ctx.extra["token_usage"]["input"] += 1200
        ctx.extra["token_usage"]["output"] += 90
        yield SSEEvent(SSEEventType.CHUNK, f"part{i} ", ctx.session_id, ctx.request_id)
    yield SSEEvent(SSEEventType.DONE, {"usage": ctx.extra["token_usage"]},
                   ctx.session_id, ctx.request_id)


async def _spending_then_crashing_engine(ctx):
    ctx.extra["token_usage"] = {"input": 800, "output": 40}
    yield SSEEvent(SSEEventType.CHUNK, "spent it ", ctx.session_id, ctx.request_id)
    raise RuntimeError("kaboom")


async def test_token_spend_is_persisted_once_per_turn():
    """Every iteration of the loop is billed, so the session's totals take the
    SUM, applied once when the turn settles — not once per iteration."""
    sm = _FakeSM()
    reg = turn_runner.TurnRegistry(sm)
    reg.start(context=_ctx("t1"), engine_factory=_spending_engine,
              format_error=str, on_complete=None)
    await asyncio.sleep(0.2)
    assert sm.usage == [(7, 2400, 180)]


async def test_token_spend_survives_a_crashed_turn():
    """Tokens burned before a failure were still billed — dropping them would
    make the counter quietly under-report exactly when things go wrong."""
    sm = _FakeSM()
    reg = turn_runner.TurnRegistry(sm)
    reg.start(context=_ctx("t2"), engine_factory=_spending_then_crashing_engine,
              format_error=str, on_complete=None)
    await asyncio.sleep(0.2)
    assert sm.usage == [(7, 800, 40)]


async def test_raised_exception_persists_partial_instead_of_vanishing():
    """A crash mid-stream used to skip persistence entirely. Now the work done
    before the crash is saved with a marker carrying the error."""
    sm = _FakeSM()
    reg = turn_runner.TurnRegistry(sm)
    reg.start(context=_ctx("e2"), engine_factory=_raise_engine,
              format_error=str, on_complete=None)
    await asyncio.sleep(0.2)
    assert len(sm.saved) == 1
    text = sm.saved[0][2][0]["text"]
    assert "did stuff " in text and "kaboom" in text


async def test_tool_only_turn_is_not_dropped():
    """A turn that ran tools but streamed no text still persists — the tool
    record is work the next turn's history must keep."""
    sm = _FakeSM()
    reg = turn_runner.TurnRegistry(sm)
    reg.start(context=_ctx("e3"), engine_factory=_tool_only_engine,
              format_error=str, on_complete=None)
    await asyncio.sleep(0.2)
    assert len(sm.saved) == 1
    content = sm.saved[0][2]
    meta = [b for b in content if b.get("type") == "_speda_meta"]
    assert meta and meta[0]["tools"][0]["id"] == "t1"


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


async def test_wait_returns_only_after_the_turn_has_settled():
    """The deterministic alternative to sleeping and hoping: when wait() returns,
    persistence and the post-turn hook have already run."""
    sm = _FakeSM()
    reg = turn_runner.TurnRegistry(sm)
    completed = []
    rid = reg.start(context=_ctx("w1"), engine_factory=_slow_engine,
                    format_error=str, on_complete=lambda: _set(completed))
    assert await reg.wait(rid, timeout=10) is True
    assert len(sm.saved) == 1 and completed == [True]
    assert reg.active() == []
    assert await reg.wait(rid, timeout=10) is True      # idempotent once done


async def test_wait_on_an_unknown_turn_reports_it_instead_of_hanging():
    reg = turn_runner.TurnRegistry(_FakeSM())
    assert await reg.wait("never-started", timeout=10) is False


async def test_wait_timing_out_does_not_kill_the_run():
    """A timeout is the waiter giving up, not a cancel — cancel() stays the only
    thing that stops a turn, so the run must still complete and persist."""
    sm = _FakeSM()
    reg = turn_runner.TurnRegistry(sm)
    rid = reg.start(context=_ctx("w2"), engine_factory=_slow_engine,
                    format_error=str, on_complete=None)
    with pytest.raises(asyncio.TimeoutError):
        await reg.wait(rid, timeout=0.01)
    assert await reg.wait(rid, timeout=10) is True
    assert sm.saved[0][2][0]["text"] == "part0 part1 part2 part3 part4 "


def _set(lst):
    async def _f():
        lst.append(True)
    return _f()
