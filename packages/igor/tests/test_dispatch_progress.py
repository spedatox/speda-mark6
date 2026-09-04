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
