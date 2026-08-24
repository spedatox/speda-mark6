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

from types import SimpleNamespace

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


def _dispatcher(engine=None, capture=None):
    d = AgentDispatcher()
    d.wire(
        orchestrator=type("O", (), {"run": staticmethod(engine or _engine(capture=capture))})(),
        profiles=_Profiles(_Profile()),
        session_manager=SessionManager(),
        ws_manager=None,
    )
    return d


async def _dispatch(maker, *, engine=None, task="Find the 2023 papers on X.", capture=None):
    d = _dispatcher(engine, capture)
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


# ── A background dispatch reports back ───────────────────────────────────────
#
# The regression: a backgrounded dispatch finished into its agent_messages
# ticket and stopped there. Nothing woke the agent that sent it, so the owner —
# who deliberately sent the job away — had to come back and ask whether it was
# done. Completion now starts a push turn on the caller, exactly as a finished
# background legionnaire does (test_legion.py).


async def _spawn(maker, d, task="Read the 2023 papers on X."):
    ticket = await d.spawn(
        from_agent="sentinel", to_agent="ultron", task=task,
        user_id=1, request_id="req-bg",
    )
    for t in list(d._background):
        await t
    return ticket


async def test_a_finished_background_dispatch_wakes_the_caller_with_the_answer(maker):
    reports = []
    d = _dispatcher()

    async def _hook(**kw):
        reports.append(kw)

    d.set_report_hook(_hook)
    outcome = await d.spawn(
        from_agent="sentinel", to_agent="ultron", task="Read the 2023 papers on X.",
        user_id=1, request_id="req-bg", origin_session_id=42,
    )
    for t in list(d._background):
        await t

    # The agent must not promise to chase it itself — the wake-up is automatic.
    assert "report back" in outcome.message
    assert len(reports) == 1
    rep = reports[0]
    assert rep["agent_id"] == "sentinel"          # who gets woken: the sender
    assert rep["to_agent"] == "ultron"            # who did the work
    assert rep["status"] == "ok"
    assert rep["result"] == "Three papers, all pre-2024."
    assert rep["ticket"] is not None
    # …and back into the chat it was ordered from, not a blank session.
    assert rep["room_session_id"] == 42


async def test_a_blocking_dispatch_does_not_report(maker):
    """Its result returns into the caller's turn, where the agent is already
    holding it and already replying — reporting would double-send."""
    reports = []
    d = _dispatcher()

    async def _hook(**kw):
        reports.append(kw)

    d.set_report_hook(_hook)
    await d.dispatch(
        from_agent="sentinel", to_agent="ultron", task="t",
        user_id=1, request_id="req-1",
    )
    assert reports == []


async def test_a_failed_background_dispatch_still_reports(maker):
    """Silence on failure is the worst outcome: the owner waits forever for a
    job that died. The report says it failed rather than not arriving."""
    def boom():
        async def run(ctx):
            raise RuntimeError("engine died")
            yield  # pragma: no cover — makes this an async generator
        return run

    reports = []
    d = _dispatcher(engine=boom())

    async def _hook(**kw):
        reports.append(kw)

    d.set_report_hook(_hook)
    await _spawn(maker, d)

    assert len(reports) == 1
    assert reports[0]["status"] == "error"
    assert "engine died" in reports[0]["result"]


# ── …and it reports into the conversation it came from ───────────────────────
#
# A report delivered into a brand-new session arrives with no thread behind it:
# the agent reads its own finding cold, having lost the owner's constraints, the
# follow-ups, and the reason the job was sent away in the first place.


class _Turns:
    """Stand-in for TurnRegistry: records the context it was handed, and can
    claim a session is busy so the wait path is exercisable."""

    def __init__(self, busy: list | None = None):
        self.started: list = []
        self._busy = list(busy or [])
        self.waited: list[str] = []

    def start(self, *, context, **kw):
        self.started.append(context)
        return context.request_id

    def active(self, *, agent_id=None, session_id=None):
        return list(self._busy)

    async def wait(self, request_id, *, timeout=None):
        self.waited.append(request_id)
        self._busy = []          # the owner's turn settles
        return True


class _ReportProfile(_Profile):
    def background_model(self, model):
        return "cheap-model"


async def _report_into(maker, room_agent: str, *, turns=None):
    """Seed a chat session belonging to `room_agent`, then report into it."""
    from app.core import trigger_runner as tr

    sm = SessionManager()
    turns = turns or _Turns()
    async with maker() as db:
        room = await sm.get_or_create(
            db=db, user_id=1, triggered_by="user", model_used="m", agent_id=room_agent,
        )
        room.title = "The owner's chat"
        await sm.save_message(db, room.id, "user", [
            {"type": "text", "text": "Have Ultron read the 2023 papers, in the background."},
        ])
        await db.commit()
        room_id = room.id

    async with maker() as db:
        started, session_id = await tr.start_trigger_turn(
            db=db,
            profile=_ReportProfile(),
            payload={"type": "dispatch_report", "to_agent": "ultron", "task": "t",
                     "result": "found Y", "status": "ok", "resumed": True},
            output_mode="push",
            request_id="req-report",
            orchestrator=None,
            turns=turns,
            session_manager=sm,
            telegram_bots=None,
            user_id=1,
            triggered_by="agent",
            session_id=room_id,
        )
    return started, session_id, room_id, turns


async def test_the_report_continues_the_thread_the_work_was_ordered_from(maker):
    _, session_id, room_id, turns = await _report_into(maker, "ultron")

    assert session_id == room_id                   # same conversation, not a new one
    context = turns.started[0]
    assert context.session_id == room_id
    # The whole thread is there: the owner's original ask, then the report seed.
    text = "\n".join(
        m["content"] if isinstance(m["content"], str)
        else "\n".join(b.get("text", "") for b in m["content"])
        for m in context.conversation_history
    )
    assert "Have Ultron read the 2023 papers" in text
    assert "BACKGROUND WORK COMPLETE" in text


async def test_a_report_never_lands_in_another_agents_conversation(maker, monkeypatch):
    """Sessions are scoped by (user, agent). A dispatched agent inherits the
    room of whoever started the chain, so reporting there would put its turn in
    another agent's transcript — and hand it that agent's history as context."""
    from app.core import trigger_runner as tr

    # The fallback session needs a name; how it is named is session_title's own
    # test (test_trigger_runner.py), and it reads a real clock.
    monkeypatch.setattr(tr, "session_title", lambda payload, today=None: "Report")
    _, session_id, room_id, turns = await _report_into(maker, "speda")

    assert session_id != room_id
    assert turns.started[0].session_id == session_id


async def test_a_report_waits_for_the_owners_live_turn_instead_of_interleaving():
    """Two turns writing one session interleave — TurnRegistry has no per-session
    lock — so a report that lands mid-conversation waits its turn."""
    from app.core.trigger_runner import _await_free_room

    turns = _Turns(busy=[{"request_id": "owner-turn", "session_id": 7}])
    room = await _await_free_room(turns, 7)

    assert room == 7
    assert turns.waited == ["owner-turn"]


async def test_a_room_that_never_frees_up_falls_back_rather_than_losing_the_report(monkeypatch):
    from app.core import trigger_runner as tr

    class _Wedged(_Turns):
        async def wait(self, request_id, *, timeout=None):
            raise TimeoutError

    monkeypatch.setattr(tr, "_ROOM_WAIT_S", 0.05)
    room = await tr._await_free_room(
        _Wedged(busy=[{"request_id": "wedged", "session_id": 7}]), 7
    )
    assert room is None          # a fresh session costs context, never the report


def test_the_seed_tells_a_resumed_report_not_to_reintroduce_itself():
    from app.core.trigger_runner import build_seed

    payload = {"type": "dispatch_report", "to_agent": "ultron", "task": "t",
               "result": "found Y", "status": "ok"}
    resumed = build_seed({**payload, "resumed": True}, "push")
    fresh = build_seed({**payload, "resumed": False}, "push")

    assert "CONTINUES the conversation above" in resumed
    assert "no reintroducing the task" in resumed
    assert "stand on its own" in fresh


# ── …and the seed never shows up as a bubble ─────────────────────────────────
#
# The seed is scaffolding: the framing, the "do not re-run the work", the raw
# findings. Rendered as a chat bubble it is a wall of prompt on top of the
# answer, attributed to an owner who never wrote it. It folds into the reply
# instead, as a card that opens like a tool call.


def _row(role: str, blocks, mid: int = 1):
    from datetime import datetime
    return SimpleNamespace(id=mid, role=role, content=blocks, created_at=datetime(2026, 8, 17, 9))


def _seed_blocks():
    from app.core.trigger_runner import report_headline, report_meta, trigger_meta

    payload = {"type": "dispatch_report", "job": "Report from ULTRON", "to_agent": "ultron",
               "task": "dig into X", "result": "found Y", "status": "ok", "ticket": 4}
    meta = trigger_meta(payload, "push")
    return [
        {"type": "text", "text": "BACKGROUND WORK COMPLETE — …findings: found Y"},
        {"type": "_speda_meta", "trigger": meta, "text": report_headline(meta["report"])},
    ]


def test_the_report_seed_folds_into_the_reply_it_produced():
    rows = rows_from_messages([
        _row("user", _seed_blocks(), 1),
        _row("assistant", [{"type": "text", "text": "Ultron found Y."}], 2),
    ])

    # One row, not two: the answer, carrying the receipt.
    assert [r["role"] for r in rows] == ["assistant"]
    assert rows[0]["content"] == "Ultron found Y."
    report = rows[0]["trigger"]["report"]
    assert report["kind"] == "dispatch"
    assert report["from"] == "ultron"
    assert report["task"] == "dig into X"
    assert report["result"] == "found Y"
    assert report["ticket"] == 4


def test_a_report_with_no_reply_is_still_in_the_transcript():
    """The run died before persisting an answer. Folding it into a reply that
    does not exist would erase the only record that the work came back."""
    rows = rows_from_messages([_row("user", _seed_blocks(), 1)])

    assert [r["role"] for r in rows] == ["user"]
    # …and it shows the headline, never the seed prompt.
    assert rows[0]["content"] == "ULTRON reported back"


def test_an_ordinary_turn_after_a_report_keeps_its_order():
    rows = rows_from_messages([
        _row("user", _seed_blocks(), 1),
        _row("user", [{"type": "text", "text": "thanks, and what about Z?"}], 2),
        _row("assistant", [{"type": "text", "text": "Z is fine."}], 3),
    ])

    assert [r["role"] for r in rows] == ["user", "user", "assistant"]
    assert rows[0]["content"] == "ULTRON reported back"     # the orphaned seed, in place
    assert "trigger" not in rows[2]                          # not folded onto the wrong reply


def test_dispatch_report_seed_forbids_redoing_the_work():
    """The normal trigger seed pushes hard on 'ACT, call real tools'. Applied to
    a finished dispatch that makes the caller send the same task again."""
    from app.core.trigger_runner import build_seed, trigger_meta

    payload = {                                    # as make_dispatch_reporter builds it
        "type": "dispatch_report", "job": "Report from ULTRON", "to_agent": "ultron",
        "task": "dig into X", "result": "found Y", "status": "ok", "ticket": 4,
    }
    seed = build_seed(payload, "push")
    assert "ALREADY DONE" in seed
    assert "found Y" in seed
    assert "ULTRON" in seed
    assert "do NOT dispatch the task again" in seed
    assert "AUTOMATED TRIGGER" not in seed      # not the execute-a-workflow seed
    # Provenance: another agent woke this turn, not the automation channel.
    meta = trigger_meta(payload, "push")
    assert meta["source"] == "agent"
    assert meta["from_agent"] == "ultron"
    assert meta["label"] == "Report from ULTRON"


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
