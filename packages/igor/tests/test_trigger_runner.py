# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

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


@pytest.fixture(autouse=True)
def post_turn_calls(monkeypatch):
    """Stub the post-turn tasks and record the calls.

    They are the chat path's work (session log, recap, title, compaction,
    embeddings) and are tested there. Left real, they open their own sessions
    against the *configured* database — `~/.speda/speda.db`, the owner's actual
    dev data, which no unit test should touch — and reach for a model. They also
    sit between the run and the settle hook every test here asserts on: measured
    3.2s to settle with them, 0.02s without.

    Yields the recorded calls so a test can assert the runner still fires them.
    """
    calls: list[tuple] = []

    async def _stub(session_id, request_id, user_id, model):
        calls.append((session_id, request_id, user_id, model))

    import app.services.memory as memory_mod

    # run_post_turn_tasks is imported inside on_complete; patch at source.
    monkeypatch.setattr(memory_mod, "run_post_turn_tasks", _stub)
    return calls


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


async def _fire(db, *, payload, output_mode="push", engine=None, bots=None,
                profile=None, agent_proxy=None, ws_manager=None):
    """Fire a triggered turn and return once it has actually settled.

    The turn is detached, and everything asserted below — the persisted rows,
    the push, the notification fallback — lands inside that task. So we wait on
    the task itself. This used to poll `active()` on a fixed 1s sleep budget,
    which made every assertion a race the test only usually won: the same two
    tests failed at random once the suite had ~170 other tests behind it.
    """
    sm = SessionManager()
    turns = TurnRegistry(sm)
    bots = bots or _Bots()
    started, session_id = await tr.start_trigger_turn(
        db=db, profile=profile or _Profile(), payload=payload, output_mode=output_mode,
        request_id="req-1", orchestrator=type("O", (), {"run": staticmethod(engine or _engine())})(),
        turns=turns, session_manager=sm, telegram_bots=bots,
        agent_proxy=agent_proxy, ws_manager=ws_manager,
    )
    assert started is not None, "registry refused the turn — nothing ran"
    # Bounded only so a genuinely hung turn fails loudly instead of hanging the
    # suite; it is not a settling budget, and a slow machine cannot exhaust it.
    assert await turns.wait(started, timeout=30)
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


async def test_post_turn_tasks_run_on_the_background_model(db, post_turn_calls):
    """The stub above is only safe if the runner is still wiring them up: a
    triggered turn gets the same session log, recap and embeddings a chat turn
    does, on the cheap tier rather than the turn's own model."""
    _, session_id, _ = await _fire(db, payload={"job": "morning_brief"})
    assert post_turn_calls == [(session_id, "req-1", 1, "test-bg-model")]


async def test_a_failed_run_skips_post_turn_work(db, post_turn_calls):
    """A half-turn must not be titled, recapped or embedded as if it completed —
    delivery still happens (below), post-turn work does not."""
    await _fire(db, payload={"job": "morning_brief"}, engine=_engine(error=True))
    assert post_turn_calls == []


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


# ── Delivery takes the answer, not the narration ────────────────────────────
# The regression: a briefing arrived on Telegram opening with "let me get the
# free news first" and "RSS store is empty, moving to deep dive". Those are the
# model's between-tool asides — fine in the transcript next to the tool cards
# they explain, stage directions when pushed to a phone with neither.

def _narrating_engine(preamble, answer):
    async def run(ctx):
        yield SSEEvent(SSEEventType.START, {}, ctx.session_id, ctx.request_id)
        yield SSEEvent(SSEEventType.CHUNK, preamble, ctx.session_id, ctx.request_id)
        yield SSEEvent(SSEEventType.TOOL, {"id": "t1", "name": "news_deep_dive", "input": {}},
                       ctx.session_id, ctx.request_id)
        yield SSEEvent(SSEEventType.CHUNK, answer, ctx.session_id, ctx.request_id)
        yield SSEEvent(SSEEventType.DONE, answer, ctx.session_id, ctx.request_id)
    return run


@pytest.mark.asyncio
async def test_push_delivers_only_the_text_written_after_the_last_tool(db):
    preamble = "Once ucretsiz haberleri alayim.\n\nRSS store bos.\n\n"
    answer = "Gunluk Briefing — 4 Agustos\n\nEnflasyon Temmuz'da hizlandi."
    _, _, bots = await _fire(
        db, payload={"job": "morning_brief"},
        engine=_narrating_engine(preamble, answer),
    )
    assert bots.sent, "nothing was delivered"
    _, delivered = bots.sent[-1]
    assert delivered == answer
    assert "RSS store" not in delivered


@pytest.mark.asyncio
async def test_transcript_keeps_the_narration_the_push_dropped(db):
    """Delivery is a view, not a redaction — the session must still show the
    whole turn, or the tool cards lose the text that explains them."""
    preamble = "Simdi takvime bakayim.\n\n"
    answer = "Bugun tek etkinlik var."
    _, session_id, _ = await _fire(
        db, payload={"job": "morning_brief"},
        engine=_narrating_engine(preamble, answer),
    )
    row = (await db.execute(
        select(Message).where(Message.session_id == session_id, Message.role == "assistant")
    )).scalars().first()
    stored = "".join(
        b["text"] for b in row.content if b.get("type") == "text"
    )
    assert stored == preamble + answer


@pytest.mark.asyncio
async def test_push_falls_back_to_the_full_turn_when_nothing_follows_the_tool(db):
    """A turn that ends right after a tool call has no closing segment.
    Delivering nothing is worse than delivering the narration."""
    async def run(ctx):
        yield SSEEvent(SSEEventType.CHUNK, "Checked, all clear.", ctx.session_id, ctx.request_id)
        yield SSEEvent(SSEEventType.TOOL, {"id": "t1", "name": "system_info", "input": {}},
                       ctx.session_id, ctx.request_id)
        yield SSEEvent(SSEEventType.DONE, "", ctx.session_id, ctx.request_id)
    _, _, bots = await _fire(db, payload={"job": "x"}, engine=run)
    assert bots.sent[-1][1] == "Checked, all clear."


@pytest.mark.asyncio
async def test_push_keeps_the_failure_marker_a_broken_turn_was_stamped_with(db):
    """The marker is appended after the last tool, so it must survive the slice
    — a briefing that broke off half-way has to arrive saying so."""
    _, _, bots = await _fire(
        db, payload={"job": "x"}, engine=_engine("Partial answer.", error=True),
    )
    assert bots.sent, "a failed turn still delivers what it had"
    assert "Partial answer." in bots.sent[-1][1]
