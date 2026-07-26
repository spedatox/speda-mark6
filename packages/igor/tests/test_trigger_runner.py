"""
Automated (n8n) turns must be ordinary chat turns.

The regression these cover: a triggered run created a session row, streamed
into a throwaway list and persisted nothing — so the owner saw a "New
conversation" in the sidebar that opened empty, the next run started with no
history, and the seed carried no timestamp, leaving date-scoped tools to guess
what "today" was. Everything here asserts parity with the chat path, plus the
one deliberate difference: the opening turn is attributed to the trigger, not
to the owner.

Runs against a real in-memory SQLite with the real SessionManager and the real
TurnRegistry; only the engine (orchestrator) and the Telegram channel are fakes.
"""

import asyncio
from datetime import date, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import trigger_runner as tr
from app.core import turn_runner
from app.core.session_manager import SessionManager
from app.core.turn_runner import TurnRegistry
from app.models.message import Message
from app.models.session import Session
from app.database import Base
from app.schemas.sse import SSEEvent, SSEEventType
from app.services.chat_history import rows_from_messages


@pytest_asyncio.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    await engine.dispose()


@pytest_asyncio.fixture
async def db(maker, monkeypatch):
    # Both the turn runner and the delivery step open their own sessions; point
    # them at this engine so the whole flow shares one database.
    monkeypatch.setattr(turn_runner, "AsyncSessionLocal", maker)
    monkeypatch.setattr(tr, "AsyncSessionLocal", maker)
    async with maker() as session:
        yield session


class _Profile:
    agent_id = "atomix"

    def allocate_model(self, triggered_by, is_background=False):
        return "test-model"

    def background_model(self, active):
        return "test-bg-model"


class _Bots:
    def __init__(self, ok=True):
        self.ok = ok
        self.sent: list[tuple[str, str]] = []

    async def deliver_message(self, agent_id, text):
        self.sent.append((agent_id, text))
        return self.ok


def _engine(text="Slept 7h12m, resting HR 54.", error=False):
    async def run(ctx):
        yield SSEEvent(SSEEventType.START, {}, ctx.session_id, ctx.request_id)
        yield SSEEvent(SSEEventType.TOOL, {"id": "t1", "name": "health_data", "input": {}},
                       ctx.session_id, ctx.request_id)
        yield SSEEvent(SSEEventType.CHUNK, text, ctx.session_id, ctx.request_id)
        if error:
            yield SSEEvent(SSEEventType.ERROR, "provider exploded", ctx.session_id, ctx.request_id)
        else:
            yield SSEEvent(SSEEventType.DONE, text, ctx.session_id, ctx.request_id)
    return run


async def _fire(db, *, payload, output_mode="push", engine=None, bots=None, post=None,
                profile=None, agent_proxy=None, ws_manager=None):
    sm = SessionManager()
    turns = TurnRegistry(sm)
    bots = bots or _Bots()
    if post is not None:
        # run_post_turn_tasks is imported inside on_complete; patch at source.
        import app.services.memory as memory_mod
        memory_mod.run_post_turn_tasks = post
    started, session_id = await tr.start_trigger_turn(
        db=db, profile=profile or _Profile(), payload=payload, output_mode=output_mode,
        request_id="req-1", orchestrator=type("O", (), {"run": staticmethod(engine or _engine())})(),
        turns=turns, session_manager=sm, telegram_bots=bots,
        agent_proxy=agent_proxy, ws_manager=ws_manager,
    )
    for _ in range(50):                      # let the detached turn settle
        await asyncio.sleep(0.02)
        if not turns.active():
            break
    return started, session_id, bots


# ── The seed ─────────────────────────────────────────────────────────────────


def test_seed_is_raw_so_load_history_can_stamp_it():
    seed = tr.build_seed({"intent": "check sleep and steps"}, "push")
    assert seed.startswith("AUTOMATED TRIGGER")     # no stamp baked in here
    assert "check sleep and steps" in seed
    assert "push notification" in seed


def test_session_is_titled_from_the_automation_not_left_as_new_conversation():
    title = tr.session_title({"job": "morning_brief"}, datetime(2026, 7, 26))
    assert title == "Morning brief · 26 Jul"
    assert tr.session_title({"type": "cron"}, datetime(2026, 7, 26)) == "Cron · 26 Jul"


def test_trigger_meta_names_the_sender():
    meta = tr.trigger_meta({"automation": "health_digest", "job": "x"}, "silent")
    assert meta["source"] == "n8n"
    assert meta["label"] == "Health digest"
    assert meta["output_mode"] == "silent"


# ── The full turn ────────────────────────────────────────────────────────────


async def test_triggered_turn_persists_both_sides_of_the_conversation(db):
    started, session_id, _ = await _fire(
        db, payload={"job": "morning_brief", "intent": "brief me"}
    )
    assert started == "req-1"

    async with db.begin_nested():
        pass
    rows = list((await db.execute(
        select(Message).where(Message.session_id == session_id).order_by(Message.id)
    )).scalars().all())

    assert [r.role for r in rows] == ["user", "assistant"]
    assert "AUTOMATED TRIGGER" in rows[0].content[0]["text"]
    assert rows[1].content[0]["text"].startswith("Slept 7h12m")
    # The tool call the run made is preserved for the transcript, as in chat.
    meta = [b for b in rows[1].content if b.get("type") == "_speda_meta"][0]
    assert meta["tools"][0]["name"] == "health_data"


async def test_the_seed_turn_is_attributed_to_the_trigger_not_the_owner(db):
    _, session_id, _ = await _fire(db, payload={"automation": "morning_brief"})
    rows = list((await db.execute(
        select(Message).where(Message.session_id == session_id).order_by(Message.id)
    )).scalars().all())

    ui = rows_from_messages(rows)
    assert ui[0]["role"] == "user"
    assert ui[0]["trigger"]["source"] == "n8n"
    assert ui[0]["trigger"]["label"] == "Morning brief"
    assert "trigger" not in ui[1]           # the agent's reply is just a reply


async def test_history_reaching_the_model_is_timestamp_stamped_like_chat(db):
    seen: dict = {}

    def capture(ctx):
        seen["history"] = ctx.conversation_history
        return _engine()(ctx)

    await _fire(db, payload={"job": "health_digest"}, engine=capture)
    first = seen["history"][0]["content"]
    text = first if isinstance(first, str) else first[0]["text"]
    assert text.startswith("[")             # "[YYYY-MM-DD HH:MM TZ] AUTOMATED…"
    assert str(date.today().year) in text[:24]
    # The display-only provenance block never reaches the model.
    assert all(
        not str(b.get("type", "")).startswith("_speda")
        for b in (first if isinstance(first, list) else [])
    )


async def test_session_gets_a_title_immediately_not_new_conversation(db):
    _, session_id, _ = await _fire(db, payload={"job": "morning_brief"})
    session = (await db.execute(select(Session).where(Session.id == session_id))).scalar_one()
    assert session.title and session.title.startswith("Morning brief")


async def test_second_run_in_the_same_session_sees_the_first(db):
    """Sessions are per-fire today, but the runner must be able to continue one —
    the whole point of persisting is that history exists to load."""
    sm = SessionManager()
    session = await sm.get_or_create(
        db=db, user_id=1, triggered_by="n8n", model_used="m", agent_id="atomix"
    )
    await sm.save_message(db, session.id, "user", "earlier turn")
    await sm.save_message(db, session.id, "assistant", "earlier answer")
    history = await sm.load_history(db, session.id)
    assert len(history) == 2 and history[0]["role"] == "user"


async def test_a_peer_backed_agent_is_triggered_on_its_peer_not_the_local_stub(db):
    """Same engine selection chat makes: an agent whose real backend is a
    connected standalone peer (Optimus/Forge) must run there on an automated
    turn too, instead of silently falling back to its in-process stub."""
    class _Peered(_Profile):
        agent_id = "optimus"
        external_backend = True

    used: list[str] = []

    class _Proxy:
        @staticmethod
        def run(ctx):
            used.append("peer")
            return _engine("peer answer")(ctx)

    _, _, bots = await _fire(
        db, payload={"job": "nightly_build"}, profile=_Peered(),
        agent_proxy=_Proxy(), ws_manager=type("W", (), {"is_connected": lambda self, a: True})(),
    )
    assert used == ["peer"]
    assert bots.sent == [("optimus", "peer answer")]


async def test_an_offline_peer_falls_back_to_the_in_process_profile(db):
    class _Peered(_Profile):
        agent_id = "optimus"
        external_backend = True

    _, _, bots = await _fire(
        db, payload={"job": "nightly_build"}, profile=_Peered(),
        agent_proxy=type("P", (), {"run": staticmethod(_engine("peer"))})(),
        ws_manager=type("W", (), {"is_connected": lambda self, a: False})(),
    )
    assert bots.sent == [("optimus", "Slept 7h12m, resting HR 54.")]


# ── Delivery ─────────────────────────────────────────────────────────────────


async def test_push_delivers_exactly_what_was_persisted(db):
    _, session_id, bots = await _fire(db, payload={"job": "morning_brief"}, output_mode="push")
    assert bots.sent == [("atomix", "Slept 7h12m, resting HR 54.")]


async def test_silent_run_stores_but_never_pushes(db):
    _, _, bots = await _fire(db, payload={"job": "health_digest"}, output_mode="silent")
    assert bots.sent == []


async def test_a_failed_run_still_delivers_the_work_it_did(db):
    """on_settle, not on_complete: a briefing that broke off half-way is worth
    delivering with its marker — dropping it silently is how a failure goes
    unnoticed for a week."""
    _, _, bots = await _fire(
        db, payload={"job": "morning_brief"}, engine=_engine(error=True)
    )
    assert len(bots.sent) == 1
    assert "Slept 7h12m" in bots.sent[0][1]
    assert "turn ended early" in bots.sent[0][1]


async def test_undeliverable_push_falls_back_to_a_notification_row(db):
    from app.models.notification import Notification

    await _fire(db, payload={"job": "morning_brief"}, bots=_Bots(ok=False))
    rows = list((await db.execute(select(Notification))).scalars().all())
    assert len(rows) == 1
    assert rows[0].source_agent == "atomix"
    assert "Slept 7h12m" in rows[0].body
