# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The persistent-reminder cycle: ask, nag, answer, give up.

These are the rules the owner actually feels — it must not go quiet early, must
not nag after being answered, and must stop at max_asks rather than forever.
"""

from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.services import reminders as R

SPEC = {
    "id": "medicine_morning",
    "text": "💊 Sabah ilacını aldın mı?",
    "at": "08:30",
    "days": "*",
    "options": [{"label": "✅ Aldım", "value": "taken"}],
    "every_minutes": 5,
    "max_asks": 3,
}


class FakeBot:
    """Records what would have gone to Telegram."""

    configured = True

    def __init__(self):
        self.sent, self.cleared, self.acks = [], [], []
        self._n = 0

    async def send_question(self, text, buttons, chat_id=None):
        self._n += 1
        self.sent.append({"text": text, "buttons": buttons})
        return str(1000 + self._n)

    async def clear_buttons(self, chat_id, message_id):
        self.cleared.append(message_id)

    async def answer_callback(self, cb_id, text=""):
        self.acks.append(text)


class FakeBots:
    def __init__(self, bot):
        self._bot = bot

    def get(self, agent_id):
        return self._bot


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_not_asked_before_its_time(db):
    bot = FakeBot()
    out = await R.tick(db, "atomix", [SPEC], FakeBots(bot), now=datetime(2026, 7, 28, 8, 0))
    assert out["waiting"] == ["medicine_morning"]
    assert bot.sent == []


@pytest.mark.asyncio
async def test_asks_once_when_due_then_holds_for_the_interval(db):
    bot, bots = FakeBot(), None
    bots = FakeBots(bot)
    t0 = datetime(2026, 7, 28, 8, 31)
    await R.tick(db, "atomix", [SPEC], bots, now=t0)
    assert len(bot.sent) == 1
    # A tick two minutes later must NOT re-ask — every_minutes is 5.
    await R.tick(db, "atomix", [SPEC], bots, now=t0 + timedelta(minutes=2))
    assert len(bot.sent) == 1
    # …but one six minutes on does.
    await R.tick(db, "atomix", [SPEC], bots, now=t0 + timedelta(minutes=6))
    assert len(bot.sent) == 2
    assert "reminder 2/3" in bot.sent[1]["text"]


@pytest.mark.asyncio
async def test_answering_stops_the_nagging(db):
    bot = FakeBot(); bots = FakeBots(bot)
    t0 = datetime(2026, 7, 28, 8, 31)
    await R.tick(db, "atomix", [SPEC], bots, now=t0)
    cycle_id, value = R.parse_callback(bot.sent[0]["buttons"][0][1])
    result = await R.answer(db, cycle_id, value, via="button", bots=bots)
    assert result["status"] == "ok" and result["answer"] == "taken"

    # Every later tick is silent, for the rest of the day.
    for minutes in (6, 60, 600):
        await R.tick(db, "atomix", [SPEC], bots, now=t0 + timedelta(minutes=minutes))
    assert len(bot.sent) == 1


@pytest.mark.asyncio
async def test_double_tap_does_not_overwrite_the_answer(db):
    bot = FakeBot(); bots = FakeBots(bot)
    await R.tick(db, "atomix", [SPEC], bots, now=datetime(2026, 7, 28, 8, 31))
    cycle_id, _ = R.parse_callback(bot.sent[0]["buttons"][0][1])
    await R.answer(db, cycle_id, "taken", bots=bots)
    again = await R.answer(db, cycle_id, "skipped", bots=bots)
    assert again["status"] == "already" and again["answer"] == "taken"


@pytest.mark.asyncio
async def test_gives_up_after_max_asks(db):
    bot = FakeBot(); bots = FakeBots(bot)
    t0 = datetime(2026, 7, 28, 8, 31)
    for i in range(6):
        await R.tick(db, "atomix", [SPEC], bots, now=t0 + timedelta(minutes=6 * i))
    assert len(bot.sent) == 3                      # max_asks
    hist = await R.history(db, "medicine_morning")
    assert hist and hist[0]["status"] == "gave_up" and hist[0]["asks"] == 3


@pytest.mark.asyncio
async def test_free_text_answer_closes_the_newest_open_reminder(db):
    bot = FakeBot(); bots = FakeBots(bot)
    await R.tick(db, "atomix", [SPEC], bots, now=datetime(2026, 7, 28, 8, 31))
    assert len(await R.list_open(db)) == 1
    out = await R.answer_latest(db, "taken", bots=bots)
    assert out["status"] == "ok"
    assert await R.list_open(db) == []
    # …and with nothing open it says so rather than inventing a close.
    assert (await R.answer_latest(db, "taken", bots=bots))["status"] == "none_open"


@pytest.mark.asyncio
async def test_a_broken_entry_cannot_stop_the_others(db):
    bot = FakeBot(); bots = FakeBots(bot)
    bad = {"id": "", "text": "no id"}
    out = await R.tick(db, "atomix", [bad, SPEC], bots, now=datetime(2026, 7, 28, 8, 31))
    assert len(bot.sent) == 1
    assert out["skipped"] and out["sent"]


# ── Agent-composed reminders (the evening-checklist path) ────────────────────
# The reminder Atomix sends is personalised in a turn, so it has no entry in any
# n8n list. It must still nag exactly like a declared one.

EVENING = "Akşam kontrol: 150 mg Lustral, 5 g kreatin, spor kartı, çanta hazır mı?"


@pytest.mark.asyncio
async def test_agent_opened_reminder_is_sent_immediately_with_buttons(db):
    bot = FakeBot(); bots = FakeBots(bot)
    out = await R.open_ask(db, agent_id="atomix", reminder_id="atomix_evening",
                           text=EVENING, options=["✅ Tamam", "⏭️ Atladım"], bots=bots)
    assert out["status"] == "ok"
    assert len(bot.sent) == 1 and EVENING in bot.sent[0]["text"]
    assert [b[0] for b in bot.sent[0]["buttons"]] == ["✅ Tamam", "⏭️ Atladım"]


@pytest.mark.asyncio
async def test_tick_keeps_asking_a_reminder_n8n_never_heard_of(db):
    """The whole point: n8n's list is EMPTY, and it still nags."""
    bot = FakeBot(); bots = FakeBots(bot)
    await R.open_ask(db, agent_id="atomix", reminder_id="atomix_evening",
                     text=EVENING, options=["✅ Tamam"], every_minutes=5, max_asks=3, bots=bots)
    t0 = R.owner_now()
    await R.tick(db, "atomix", [], bots, now=t0 + timedelta(minutes=1))   # too soon
    assert len(bot.sent) == 1
    await R.tick(db, "atomix", [], bots, now=t0 + timedelta(minutes=6))   # due
    assert len(bot.sent) == 2 and "reminder 2/3" in bot.sent[1]["text"]
    assert EVENING in bot.sent[1]["text"], "the personalised text must be re-sent verbatim"


@pytest.mark.asyncio
async def test_agent_opened_reminder_stops_when_answered(db):
    bot = FakeBot(); bots = FakeBots(bot)
    await R.open_ask(db, agent_id="atomix", reminder_id="atomix_evening",
                     text=EVENING, options=["✅ Tamam"], max_asks=5, bots=bots)
    cycle_id, value = R.parse_callback(bot.sent[0]["buttons"][0][1])
    assert (await R.answer(db, cycle_id, value, bots=bots))["status"] == "ok"
    t0 = R.owner_now()
    for m in (6, 12, 60):
        await R.tick(db, "atomix", [], bots, now=t0 + timedelta(minutes=m))
    assert len(bot.sent) == 1


@pytest.mark.asyncio
async def test_agent_opened_reminder_gives_up(db):
    bot = FakeBot(); bots = FakeBots(bot)
    await R.open_ask(db, agent_id="atomix", reminder_id="atomix_evening",
                     text=EVENING, every_minutes=5, max_asks=2, bots=bots)
    t0 = R.owner_now()
    for i in range(1, 5):
        await R.tick(db, "atomix", [], bots, now=t0 + timedelta(minutes=6 * i))
    assert len(bot.sent) == 2
    hist = await R.history(db, "atomix_evening")
    assert hist[0]["status"] == "gave_up"


@pytest.mark.asyncio
async def test_a_retried_trigger_cannot_open_two_questions(db):
    bot = FakeBot(); bots = FakeBots(bot)
    a = await R.open_ask(db, agent_id="atomix", reminder_id="atomix_evening", text=EVENING, bots=bots)
    b = await R.open_ask(db, agent_id="atomix", reminder_id="atomix_evening", text=EVENING, bots=bots)
    assert a["status"] == "ok" and b["status"] == "already_open"
    assert len(bot.sent) == 1


@pytest.mark.asyncio
async def test_another_agents_tick_does_not_touch_it(db):
    bot = FakeBot(); bots = FakeBots(bot)
    await R.open_ask(db, agent_id="atomix", reminder_id="atomix_evening", text=EVENING, bots=bots)
    await R.tick(db, "speda", [], bots, now=R.owner_now() + timedelta(minutes=30))
    assert len(bot.sent) == 1, "Speda's tick must not re-ask Atomix's reminder"


# ── The ledger Atomix reads back ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_history_records_taken_and_missed_days(db):
    """'Did I take it Tuesday?' must be answerable from data, not memory."""
    bot = FakeBot(); bots = FakeBots(bot)
    spec = dict(SPEC, id="lustral", max_asks=2)

    # Day 1: answered.
    d1 = datetime(2026, 7, 20, 8, 31)
    await R.tick(db, "atomix", [spec], bots, now=d1)
    cid, _ = R.parse_callback(bot.sent[-1]["buttons"][0][1])
    await R.answer(db, cid, "taken", bots=bots)

    # Day 2: ignored until it gave up.
    d2 = datetime(2026, 7, 21, 8, 31)
    for i in range(4):
        await R.tick(db, "atomix", [spec], bots, now=d2 + timedelta(minutes=6 * i))

    rows = await R.history(db, "lustral")
    by_day = {r["day"]: r for r in rows}
    assert by_day["2026-07-20"]["status"] == "answered"
    assert by_day["2026-07-20"]["answer"] == "taken"
    assert by_day["2026-07-21"]["status"] == "gave_up"
    assert by_day["2026-07-21"]["asks"] == 2


# ── Definitions: the app's Reminders settings section ────────────────────────

@pytest.mark.asyncio
async def test_definition_round_trips(db):
    out = await R.upsert_definition(db, {
        "id": "lustral", "agent": "atomix", "text": "💊 150 mg Lustral aldın mı?",
        "at": "23:00", "days": "*", "options": ["✅ Aldım", "⏭️ Atladım"],
        "every_minutes": 5, "max_asks": 10,
    })
    assert out["status"] == "ok"
    rows = await R.list_definitions(db, agent_id="atomix")
    assert len(rows) == 1 and rows[0]["id"] == "lustral"
    assert [o["label"] for o in rows[0]["options"]] == ["✅ Aldım", "⏭️ Atladım"]
    # A second upsert with the same id edits rather than duplicating.
    await R.upsert_definition(db, {"id": "lustral", "agent": "atomix",
                                   "text": "changed", "at": "22:00"})
    rows = await R.list_definitions(db, agent_id="atomix")
    assert len(rows) == 1 and rows[0]["text"] == "changed" and rows[0]["at"] == "22:00"


@pytest.mark.asyncio
async def test_stored_definition_fires_without_any_n8n_entry(db):
    bot = FakeBot(); bots = FakeBots(bot)
    await R.upsert_definition(db, {"id": "lustral", "agent": "atomix", "text": "💊 Lustral",
                                   "at": "08:30", "options": ["✅ Aldım"], "max_asks": 2})
    # n8n sends an EMPTY list — the definition alone must drive the ask.
    await R.tick(db, "atomix", [], bots, now=datetime(2026, 7, 28, 8, 31))
    assert len(bot.sent) == 1 and "Lustral" in bot.sent[0]["text"]


@pytest.mark.asyncio
async def test_disabled_definition_does_not_fire(db):
    bot = FakeBot(); bots = FakeBots(bot)
    await R.upsert_definition(db, {"id": "lustral", "agent": "atomix", "text": "💊 Lustral",
                                   "at": "08:30", "enabled": False})
    await R.tick(db, "atomix", [], bots, now=datetime(2026, 7, 28, 8, 31))
    assert bot.sent == []


@pytest.mark.asyncio
async def test_stored_definition_shadows_an_inline_one_with_the_same_id(db):
    """Both surfaces still work, but the one the owner edits in the app wins —
    and it must NOT be asked twice."""
    bot = FakeBot(); bots = FakeBots(bot)
    await R.upsert_definition(db, {"id": "medicine_morning", "agent": "atomix",
                                   "text": "FROM THE APP", "at": "08:30"})
    await R.tick(db, "atomix", [SPEC], bots, now=datetime(2026, 7, 28, 8, 31))
    assert len(bot.sent) == 1, "one reminder, not one per source"
    assert "FROM THE APP" in bot.sent[0]["text"]


@pytest.mark.asyncio
async def test_deleting_a_definition_keeps_its_history(db):
    bot = FakeBot(); bots = FakeBots(bot)
    await R.upsert_definition(db, {"id": "lustral", "agent": "atomix", "text": "💊 Lustral",
                                   "at": "08:30", "max_asks": 1})
    await R.tick(db, "atomix", [], bots, now=datetime(2026, 7, 28, 8, 31))
    cid, _ = R.parse_callback(bot.sent[0]["buttons"][0][1])
    await R.answer(db, cid, "taken", bots=bots)

    assert (await R.delete_definition(db, "lustral"))["status"] == "ok"
    assert await R.list_definitions(db, agent_id="atomix") == []
    hist = await R.history(db, "lustral")
    assert hist and hist[0]["answer"] == "taken", "history must survive the delete"
