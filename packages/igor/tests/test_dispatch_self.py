# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`allow_self` — the one caller allowed to dispatch an agent to itself.

`_precheck` has always refused `to_agent == from_agent`: a model deciding to
dispatch a copy of itself is a loop risk with no legitimate use, so
`dispatch_agent` (the model-facing tool) has never been able to do it and must
never gain the ability by accident.

The new `/bg <task>` command (Telegram gateway and the web chat router) is a
different caller entirely: the OWNER, already authenticated before the command
is even parsed, typing directly to the agent they want to run the task. There
"dispatch to yourself" is the whole request, not a model's judgment call, so it
passes `allow_self=True` — the one explicit, narrow bypass. These tests pin
both halves: the default stays refused, and only an explicit `allow_self=True`
opens it, with the background run and its self-targeted report actually
working end to end.
"""
from types import SimpleNamespace

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import dispatch as dp
from app.core.dispatch import AgentDispatcher, BG_COMMAND, SpawnOutcome, bg_ack
from app.core.session_manager import SessionManager
from app.database import Base
from app.schemas.sse import SSEEvent, SSEEventType


@pytest_asyncio.fixture
async def maker(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(dp, "AsyncSessionLocal", async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession,
    ))
    yield async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    await engine.dispose()


class _Profile:
    agent_id = "ultron"
    dispatch_target = True

    def allocate_model(self, kind):
        return "test-model"


class _Profiles:
    def __init__(self, profile):
        self._p = profile

    def get(self, agent_id):
        return self._p if agent_id == self._p.agent_id else None

    def roster(self):
        return [self._p]


def _engine(text="Done."):
    async def run(ctx):
        yield SSEEvent(SSEEventType.CHUNK, text, ctx.session_id, ctx.request_id)
        yield SSEEvent(SSEEventType.DONE, text, ctx.session_id, ctx.request_id)
    return run


def _dispatcher():
    d = AgentDispatcher()
    d.wire(
        orchestrator=type("O", (), {"run": staticmethod(_engine())})(),
        profiles=_Profiles(_Profile()),
        session_manager=SessionManager(),
        ws_manager=None,
    )
    return d


# ── bg_ack: the owner-facing reply, pure ─────────────────────────────────────


def test_bg_ack_does_not_echo_the_task_back():
    """The owner just typed the task — repeating it back is noise. The ack must
    confirm and hand over a status handle, never parrot the request."""
    task = "prototype a habit tracker and deposit it when done"
    ack = bg_ack(SpawnOutcome(started=True, message="(model prose)", ticket_id=42))
    assert task not in ack
    assert "(model prose)" not in ack, "the model-facing prose is not for the owner"
    assert "#42" in ack, "the ack hands over the ticket so status is checkable"


def test_bg_ack_shows_the_refusal_reason_verbatim_on_failure():
    ack = bg_ack(SpawnOutcome(started=False, message="Refused: over the cap."))
    assert ack == "Refused: over the cap."


def test_bg_ack_without_a_ticket_id_still_confirms():
    ack = bg_ack(SpawnOutcome(started=True, message="x", ticket_id=None))
    assert "background" in ack.lower()
    assert "#" not in ack


def test_bg_command_is_a_single_source_of_truth():
    """Both surfaces import this; it must be the literal they parse."""
    assert BG_COMMAND == "/bg"


# ── _precheck: pure, no DB ───────────────────────────────────────────────────


def test_precheck_refuses_self_dispatch_by_default():
    d = AgentDispatcher()
    d._orchestrator = object()  # only needs to be non-None here
    msg = d._precheck("ultron", "ultron", 0)
    assert msg is not None
    assert "cannot dispatch a task to yourself" in msg


def test_precheck_allows_self_dispatch_when_explicitly_requested():
    d = AgentDispatcher()
    d._orchestrator = object()
    assert d._precheck("ultron", "ultron", 0, allow_self=True) is None


def test_precheck_still_refuses_other_pairs_normally():
    """allow_self must not accidentally loosen the depth limit or anything
    else — it only touches the self-dispatch line."""
    d = AgentDispatcher()
    d._orchestrator = object()
    from app.core.dispatch import MAX_DISPATCH_DEPTH
    msg = d._precheck("sentinel", "ultron", MAX_DISPATCH_DEPTH, allow_self=True)
    assert msg is not None
    assert "depth limit" in msg


# ── spawn(): end to end ──────────────────────────────────────────────────────


async def test_spawn_refuses_self_dispatch_by_default(maker):
    d = _dispatcher()
    outcome = await d.spawn(
        from_agent="ultron", to_agent="ultron", task="do a thing",
        user_id=1, request_id="req-self-1",
    )
    assert outcome.started is False
    assert outcome.ticket_id is None
    assert "cannot dispatch a task to yourself" in outcome.message
    assert len(d._background) == 0, "a refused dispatch must not start a background task"


async def test_spawn_allows_self_dispatch_with_allow_self(maker):
    reports = []
    d = _dispatcher()

    async def _hook(**kw):
        reports.append(kw)

    d.set_report_hook(_hook)
    outcome = await d.spawn(
        from_agent="ultron", to_agent="ultron", task="prototype something",
        user_id=1, request_id="req-self-2", origin_session_id=7,
        allow_self=True,
    )
    assert outcome.started is True
    assert outcome.ticket_id is not None
    for t in list(d._background):
        await t

    # The self-targeted report wakes the SAME agent that was dispatched — the
    # architecture already supports this (see trigger_runner.py's room-mismatch
    # fallback); allow_self just permits reaching it.
    assert len(reports) == 1
    rep = reports[0]
    assert rep["agent_id"] == "ultron"
    assert rep["to_agent"] == "ultron"
    assert rep["status"] == "ok"
    assert rep["result"] == "Done."
    assert rep["room_session_id"] == 7
