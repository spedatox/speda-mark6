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
