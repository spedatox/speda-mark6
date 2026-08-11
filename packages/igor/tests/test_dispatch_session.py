"""
A dispatched run must be a readable conversation on the target agent.

The regression these cover: an agent-to-agent dispatch created a session row on
the target, streamed the run into a throwaway list and persisted nothing — so
the owner opened that agent's sidebar, found a "New conversation", clicked it,
and got an empty transcript. The work had happened; only the comms-tray ticket
survived it. This is the same bug an n8n-triggered run had (see
test_trigger_runner.py), and it is fixed the same way: persist both sides, name
the session at launch, and attribute the opening turn to the agent that sent it
rather than to the owner.

Runs against a real in-memory SQLite with the real SessionManager; only the
engine (orchestrator) and the profile registry are fakes.
"""

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import dispatch as dp
from app.core.dispatch import AgentDispatcher
from app.core.session_manager import SessionManager
from app.database import Base
from app.models.message import Message
from app.models.session import Session
from app.schemas.sse import SSEEvent, SSEEventType
from app.services.chat_history import rows_from_messages


@pytest_asyncio.fixture
async def maker(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Every DB session the dispatcher opens (the run, the channel transcript,
    # the agent_messages telemetry) comes from here.
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


def _engine(text="Three papers, all pre-2024.", error=None, capture=None):
    async def run(ctx):
        if capture is not None:
            capture["context"] = ctx
        yield SSEEvent(SSEEventType.START, {}, ctx.session_id, ctx.request_id)
        yield SSEEvent(SSEEventType.TOOL, {"id": "t1", "name": "arxiv_search", "input": {"q": "x"}},
                       ctx.session_id, ctx.request_id)
        yield SSEEvent(SSEEventType.TOOL_RESULT, {"id": "t1", "result": "3 hits"},
                       ctx.session_id, ctx.request_id)
        yield SSEEvent(SSEEventType.CHUNK, text, ctx.session_id, ctx.request_id)
        if error:
            yield SSEEvent(SSEEventType.ERROR, error, ctx.session_id, ctx.request_id)
        else:
            yield SSEEvent(SSEEventType.DONE, text, ctx.session_id, ctx.request_id)
    return run


async def _dispatch(maker, *, engine=None, task="Find the 2023 papers on X.", capture=None):
    d = AgentDispatcher()
    d.wire(
        orchestrator=type("O", (), {"run": staticmethod(engine or _engine(capture=capture))})(),
        profiles=_Profiles(_Profile()),
        session_manager=SessionManager(),
        ws_manager=None,
    )
    result = await d.dispatch(
        from_agent="sentinel", to_agent="ultron", task=task,
        user_id=1, request_id="req-1",
    )
    async with maker() as db:
        session = (await db.execute(select(Session))).scalars().one()
        rows = list((await db.execute(
            select(Message).where(Message.session_id == session.id).order_by(Message.id)
        )).scalars().all())
    return result, session, rows


# ── Naming and provenance (pure) ─────────────────────────────────────────────


def test_session_is_named_from_the_sender_not_left_as_new_conversation():
    title = dp.session_title("sentinel", "Check whether the BTC position is still  hedged")
    assert title == "SENTINEL → Check whether the BTC position is still hedged"
    assert len(dp.session_title("speda", "x" * 400)) <= 255


def test_dispatch_meta_names_the_calling_agent():
    meta = dp.dispatch_meta("nightcrawler", house_party=False)
    assert meta["source"] == "agent"
    assert meta["from_agent"] == "nightcrawler"
    assert meta["label"] == "Dispatch from NIGHTCRAWLER"
    assert meta["job"] == "dispatch"
    assert dp.dispatch_meta("speda", house_party=True)["job"] == "house party dispatch"


# ── The full dispatch ────────────────────────────────────────────────────────


async def test_dispatched_run_persists_both_sides_of_the_conversation(maker):
    result, session, rows = await _dispatch(maker)

    assert result == "Three papers, all pre-2024."
    assert session.title == "SENTINEL → Find the 2023 papers on X."
    assert [r.role for r in rows] == ["user", "assistant"]
    assert "Inter-agent dispatch from SENTINEL" in rows[0].content[0]["text"]
    assert rows[1].content[0]["text"] == "Three papers, all pre-2024."
    # The tool calls the run made are preserved for the transcript, as in chat.
    meta = [b for b in rows[1].content if b.get("type") == "_speda_meta"][0]
    assert meta["tools"][0]["name"] == "arxiv_search"
    assert meta["tools"][0]["result"] == "3 hits"


async def test_the_opening_turn_is_attributed_to_the_calling_agent(maker):
    _, _, rows = await _dispatch(maker)

    ui = rows_from_messages(rows)
    assert ui[0]["role"] == "user"
    assert ui[0]["trigger"]["source"] == "agent"
    assert ui[0]["trigger"]["from_agent"] == "sentinel"
    # The bubble shows the task, not the routing preamble the model reads.
    assert ui[0]["content"] == "Find the 2023 papers on X."
    assert "trigger" not in ui[1]           # the agent's reply is just a reply


async def test_history_reaching_the_model_is_stamped_and_carries_no_meta(maker):
    capture: dict = {}
    await _dispatch(maker, capture=capture)

    history = capture["context"].conversation_history
    assert len(history) == 1 and history[0]["role"] == "user"
    first = history[0]["content"]
    text = first if isinstance(first, str) else first[0]["text"]
    assert text.startswith("[")             # "[Tue 2026-08-11 14:03 +03] Inter-agent…"
    # The display-only provenance block never reaches the model.
    assert all(
        not str(b.get("type", "")).startswith("_speda")
        for b in (first if isinstance(first, list) else [])
    )


async def test_a_failed_run_still_leaves_its_transcript_behind(maker):
    result, _, rows = await _dispatch(maker, engine=_engine(error="provider exploded"))

    # The caller still gets the text (the engine produced some) — but the
    # transcript shows the turn broke off rather than reading as complete.
    assert result == "Three papers, all pre-2024."
    assert [r.role for r in rows] == ["user", "assistant"]
    assert "turn ended early" in rows[1].content[0]["text"]
    assert "provider exploded" in rows[1].content[0]["text"]


async def test_a_dispatch_that_raises_does_not_leave_an_empty_session(maker):
    def boom():
        async def run(ctx):
            yield SSEEvent(SSEEventType.CHUNK, "partial work", ctx.session_id, ctx.request_id)
            raise RuntimeError("engine died")
        return run

    result, _, rows = await _dispatch(maker, engine=boom())

    assert "failed" in result.lower()
    assert [r.role for r in rows] == ["user", "assistant"]
    assert rows[1].content[0]["text"] == "partial work"
