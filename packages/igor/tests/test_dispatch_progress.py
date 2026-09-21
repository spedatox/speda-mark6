# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A background dispatch to an external peer streams live progress.

The regression these close: `task_dispatch` is fire-and-await — one frame out,
one `task_result` back — so a backgrounded Optimus job was a black box. The
owner got a tray row that said "running" and nothing else for minutes, with no
way to tell real work from a wedge, while an in-process legionnaire doing the
same thing showed every tool call live.

The peer now also emits `task_event` frames, and they land in the SAME
`LegionRunRegistry` the background legionnaires stream into — which is what
lets the existing tray row and the existing `/legion/attach/{ticket}` endpoint
render a peer job with no second endpoint and no client change.

Only the registry and the WebSocket manager are real here; the peer is a stub
that streams whatever the test hands it.
"""

import asyncio

import pytest

from app.core.dispatch import AgentDispatcher
from app.legion.run_registry import LegionRunRegistry


class _Manager:
    """A connected peer that records what was dispatched to it."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    def is_connected(self, agent_id, host=None) -> bool:
        return True

    def peers(self, agent_id):
        # One peer, no declared roots — takes anything (app/core/peer_routing.py).
        from app.core.peer_routing import PeerInfo
        return [PeerInfo(agent_id="optimus", host="server", platform="linux", roots=[])]

    async def send(self, agent_id, frame, host=None) -> None:
        self.sent.append(frame)


def _dispatcher(runs: LegionRunRegistry) -> AgentDispatcher:
    d = AgentDispatcher()
    d.wire(orchestrator=None, profiles=None, session_manager=None,
           ws_manager=_Manager(), runs=runs)
    return d


@pytest.mark.asyncio
async def test_peer_progress_reaches_the_ticket_the_client_attaches_to():
    runs = LegionRunRegistry()
    d = _dispatcher(runs)
    runs.register(7, agent="optimus", label="refactor", room_session_id=3)

    # The dispatch is in flight: _run_external has correlated its wire task_id
    # to the tray ticket.
    d._external_tickets["wire-1"] = 7

    seen: list[dict] = []

    async def watcher():
        async for event in runs.subscribe(7):
            seen.append(event)

    task = asyncio.create_task(watcher())
    await asyncio.sleep(0)

    assert d.deliver_task_event("wire-1", {"type": "tool", "data": {"name": "read_file"}})
    assert d.deliver_task_event("wire-1", {"type": "chunk", "data": "reading"})
    runs.finish(7, ok=True)
    await asyncio.wait_for(task, timeout=1.0)

    assert [e["type"] for e in seen] == ["tool", "chunk"], (
        f"the attached client must see the peer's progress; got {seen}"
    )


def test_an_untracked_task_is_dropped_rather_than_raising():
    """A synchronous dispatch has no tray row, and the peer streams anyway —
    it cannot know which of its jobs the backend is showing."""
    runs = LegionRunRegistry()
    d = _dispatcher(runs)

    assert d.deliver_task_event("never-registered", {"type": "chunk", "data": "x"}) is False


def test_progress_is_inert_when_no_registry_is_wired():
    """Many callers wire a bare dispatcher; it must behave exactly as before."""
    d = AgentDispatcher()
    d.wire(orchestrator=None, profiles=None, session_manager=None, ws_manager=_Manager())
    d._external_tickets["wire-1"] = 7

    assert d.deliver_task_event("wire-1", {"type": "chunk", "data": "x"}) is False
    d._finish_run(7, ok=True)   # must not raise


@pytest.mark.asyncio
async def test_registry_replay_preserves_parallel_tool_correlation():
    runs = LegionRunRegistry()
    runs.register(9, agent="general", label="parallel", room_session_id=3)
    expected = [
        {"phase": "tool", "tool_call_id": "a", "tool": "first"},
        {"phase": "tool", "tool_call_id": "b", "tool": "second"},
        {"phase": "tool_result", "tool_call_id": "b", "tool": "second", "result": "B"},
        {"phase": "tool_result", "tool_call_id": "a", "tool": "first", "result": "A"},
    ]
    for event in expected:
        runs.emit(9, event)
    runs.finish(9, ok=True)

    replayed = [event async for event in runs.subscribe(9)]
    assert replayed == expected


@pytest.mark.asyncio
async def test_the_ticket_correlation_is_dropped_when_the_job_ends(monkeypatch):
    """Otherwise the map grows by one entry per dispatch, forever.

    Driven through the timeout path: nothing answers the dispatch, which is the
    exit most likely to skip cleanup, and the one a wedged peer actually takes.
    """
    from app.core import dispatch as dp
    monkeypatch.setattr(dp, "EXTERNAL_CODING_TIMEOUT_S", 0.05)

    runs = LegionRunRegistry()
    d = _dispatcher(runs)

    result, status = await d._run_external(
        to_agent="optimus", from_agent="igor", task="do a thing", ticket=11,
    )
    assert status == "timeout"

    assert d._external_tickets == {}, (
        "the wire-id → ticket entry must be released with the dispatch"
    )


@pytest.mark.asyncio
async def test_in_process_dispatch_progress_emits_to_run_registry_and_turns(monkeypatch):
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.database import Base
    from app.models.agent_message import AgentMessage
    from app.schemas.sse import SSEEvent, SSEEventType
    from app.core.session_manager import SessionManager
    from app.core.turn_runner import TurnRegistry
    from app.core import dispatch as dp

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(dp, "AsyncSessionLocal", maker)

    runs = LegionRunRegistry()
    session_mgr = SessionManager()
    turns = TurnRegistry(session_mgr)

    class _Profile:
        agent_id = "ultron"
        dispatch_target = True

        def allocate_model(self, kind):
            return "test-model"

    class _Profiles:
        def get(self, aid):
            return _Profile() if aid == "ultron" else None

        def roster(self):
            return [_Profile()]

    async def _mock_run(ctx):
        yield SSEEvent(SSEEventType.START, {}, ctx.session_id, ctx.request_id)
        yield SSEEvent(
            SSEEventType.TOOL,
            {"id": "t1", "name": "arxiv_search", "input": {"q": "ai"}},
            ctx.session_id,
            ctx.request_id,
        )
        yield SSEEvent(
            SSEEventType.TOOL_RESULT,
            {"id": "t1", "result": "done"},
            ctx.session_id,
            ctx.request_id,
        )
        yield SSEEvent(SSEEventType.CHUNK, "Found papers.", ctx.session_id, ctx.request_id)
        yield SSEEvent(SSEEventType.DONE, "Found papers.", ctx.session_id, ctx.request_id)

    d = AgentDispatcher()
    d.wire(
        orchestrator=type("O", (), {"run": staticmethod(_mock_run)})(),
        profiles=_Profiles(),
        session_manager=session_mgr,
        ws_manager=None,
        runs=runs,
        turns=turns,
    )

    emitted = []

    def on_emit(ev):
        emitted.append(ev)

    res = await d.dispatch(
        from_agent="sentinel",
        to_agent="ultron",
        task="Search arxiv",
        user_id=1,
        request_id="req-p1",
        emit=on_emit,
        tool_call_id="call-1",
    )

    assert "Found papers." in res

    # 1. Verify emit callback received progress phases
    phases = [e.get("phase") for e in emitted]
    assert phases == ["started", "tool", "tool_result", "text", "finished"]
    assert emitted[0]["id"] == "call-1"
    assert emitted[1]["tool"] == "arxiv_search"

    # 2. Verify AgentMessage in DB has session_id set
    async with maker() as db:
        msg = (await db.execute(select(AgentMessage))).scalars().first()
        assert msg is not None
        assert msg.session_id is not None
        assert msg.status == "ok"

    await engine.dispose()

