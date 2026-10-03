# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Channel selection survives delivery, restarts, resets and session deletion."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete, event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.core.session_manager import SessionManager
from app.database import Base
from app.models.session import Session
from app.models.user import User


@pytest.fixture
async def maker(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'channels.db'}")

    @event.listens_for(engine.sync_engine, "connect")
    def configure(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add(User(id=1, name="owner", timezone="UTC"))
        await db.commit()
    yield factory
    await engine.dispose()


async def _session(sm, db, agent="ultron", channel="telegram"):
    return await sm.get_or_create(
        db=db, user_id=1, agent_id=agent, channel=channel,
        triggered_by="user", model_used="test-model",
    )


async def test_adopts_latest_legacy_channel_session_once(maker):
    async with maker() as db:
        older = Session(user_id=1, agent_id="ultron", channel="telegram",
                        triggered_by="user", model_used="m",
                        started_at=datetime(2026, 10, 1))
        latest = Session(user_id=1, agent_id="ultron", channel="telegram",
                         triggered_by="user", model_used="m",
                         started_at=older.started_at + timedelta(days=1))
        db.add_all([older, latest])
        await db.commit()
        sm = SessionManager()
        assert (await _session(sm, db)).id == latest.id
        await sm.reset_channel_session(db, "telegram", "ultron")
        fresh = await _session(SessionManager(), db)
        assert fresh.id not in {older.id, latest.id}
        assert older.ended_at is not None and latest.ended_at is not None


async def test_binding_rejects_other_user_agent_closed_or_missing_session(maker):
    async with maker() as db:
        sm = SessionManager()
        selected = await _session(sm, db)
        other = await _session(sm, db, agent="sentinel", channel="app")
        assert not await sm.bind_channel_session(db, "telegram", "ultron", other.id)
        assert not await sm.bind_channel_session(db, "telegram", "ultron", selected.id, 2)
        assert not await sm.bind_channel_session(db, "telegram", "ultron", 99999)
        closed = await _session(sm, db, channel="app")
        await sm.close(db, closed.id)
        assert not await sm.bind_channel_session(db, "telegram", "ultron", closed.id)
        assert (await _session(sm, db)).id == selected.id


async def test_deleting_selected_session_clears_pointer_without_reviving_old_chat(maker):
    async with maker() as db:
        sm = SessionManager()
        old = await _session(sm, db)
        selected = await _session(sm, db, channel="app")
        assert await sm.bind_channel_session(db, "telegram", "ultron", selected.id)
        # Production enables FK enforcement; a channel pointer must not block
        # deleting a conversation or leave a reference to a deleted session.
        await db.execute(delete(Session).where(Session.id == selected.id))
        await db.commit()
        fresh = await _session(SessionManager(), db)
        assert fresh.id != old.id
        assert fresh.channel == "telegram"


async def test_ingress_sees_selection_written_by_another_manager(maker):
    reader, writer = SessionManager(), SessionManager()
    async with maker() as db:
        old = await _session(reader, db)
        pushed = await _session(writer, db, channel="app")
    async with maker() as delivery_db:
        assert await writer.bind_channel_session(delivery_db, "telegram", "ultron", pushed.id)
    async with maker() as ingress_db:
        assert (await _session(reader, ingress_db)).id == pushed.id
        assert pushed.id != old.id

@pytest.mark.parametrize("kind", ["message", "file", "reminder"])
@pytest.mark.parametrize("delivered", [True, False])
async def test_tool_delivery_selects_source_and_keeps_sent_text(maker, tmp_path, monkeypatch,
                                                               kind, delivered):
    from app.core.context import AgentContext
    from app.skills import telegram as skills
    from app.skills.reminders import RemindersSkill
    from app.telegram import registry as registry_module

    class Bot:
        configured = True

        async def send_message(self, text, **kwargs):
            return delivered

        async def send_document(self, path, **kwargs):
            return delivered

        async def send_question(self, text, buttons):
            return "123" if delivered else None

    monkeypatch.setattr(registry_module, "get_telegram_started", lambda: {})
    async with maker() as db:
        sm = SessionManager()
        old = await _session(sm, db)
        source = await _session(sm, db, channel="app")
        await sm.save_message(db, source.id, "user", "Prepare an update and send it.")
        bots = registry_module.TelegramBotRegistry()
        bots.wire(session_manager=sm)
        bots._bots["ultron"] = Bot()
        context = AgentContext(
            agent_id="ultron", user_id=1, session_id=source.id,
            request_id="tool-delivery", triggered_by="n8n", trigger_payload={},
            output_mode="silent", model="test-model", system_prompt="",
            conversation_history=[], db=db,
        )
        text = "Your latest research update."
        if kind == "message":
            await skills.SendTelegramMessageSkill(bots).execute({"text": text}, context)
        elif kind == "file":
            path = tmp_path / "report.txt"
            path.write_text("Report content", encoding="utf-8")
            monkeypatch.setattr(skills, "safe_output_path", lambda name: path)
            await skills.SendTelegramFileSkill(bots).execute(
                {"filename": "report.txt", "caption": text}, context,
            )
        else:
            await RemindersSkill(bots).execute(
                {"action": "ask", "text": text, "reminder_id": "research-check"}, context,
            )
        selected = await _session(SessionManager(), db)
        assert selected.id == (source.id if delivered else old.id)
        history = str(await sm.load_history(db, source.id))
        assert (text in history) == delivered
