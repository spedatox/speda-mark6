"""Cross-chat continuity and provenance-backed links survive missing recaps."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models
from app.core.clock import owner_tz
from app.database import Base
from app.models.message import Message
from app.models.observation import Observation
from app.models.session import Session
from app.services.observations import related_observations
from app.services.relevant_recall import facts_for_query
from app.skills.memory import today_across_sessions_for_context
from app.skills.observations import SearchMemorySkill
from app.skills.observations import RecordObservationSkill


@pytest.fixture
async def sessions():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_today_continuity_survives_missing_recap_and_respects_scope(sessions):
    now = datetime.now(timezone.utc)
    async with sessions() as db:
        old = Session(user_id=1, agent_id="ultron", triggered_by="user", model_used="test")
        current = Session(user_id=1, agent_id="ultron", triggered_by="user", model_used="test")
        speda = Session(user_id=1, agent_id="speda", triggered_by="user", model_used="test")
        other_owner = Session(user_id=2, agent_id="ultron", triggered_by="user", model_used="test")
        background = Session(user_id=1, agent_id="ultron", triggered_by="n8n", model_used="test")
        db.add_all([old, current, speda, other_owner, background])
        await db.flush()
        db.add_all([
            Message(session_id=old.id, role="user", content="ATA101 sınavı cuma.", created_at=now),
            Message(session_id=current.id, role="user", content="Current private message", created_at=now),
            Message(session_id=speda.id, role="user", content="Bedirhan taşındı.", created_at=now),
            Message(session_id=other_owner.id, role="user", content="Other user's secret", created_at=now),
            Message(session_id=background.id, role="user", content="Nightly automation", created_at=now),
        ])
        await db.commit()
        own = await today_across_sessions_for_context(1, db, "ultron", current.id)
        assert "ATA101 sınavı cuma" in own
        assert "Bedirhan" not in own
        assert "Current private" not in own
        assert "Other user's secret" not in own
        assert "Nightly automation" not in own
        all_agents = await today_across_sessions_for_context(
            1, db, "ultron", current.id, scope="all",
        )
        assert "Bedirhan taşındı" in all_agents
        assert "ATA101 sınavı cuma" in all_agents
        assert old.recap is None


async def test_today_continuity_uses_owner_day_boundary(sessions):
    now = datetime.now(timezone.utc)
    local_start = datetime.combine(now.astimezone(owner_tz()).date(), datetime.min.time(),
                                   tzinfo=owner_tz()).astimezone(timezone.utc)
    async with sessions() as db:
        old = Session(user_id=1, agent_id="ultron", triggered_by="user", model_used="test")
        current = Session(user_id=1, agent_id="ultron", triggered_by="user", model_used="test")
        db.add_all([old, current])
        await db.flush()
        db.add_all([
            Message(session_id=old.id, role="user", content="Yesterday's lecture",
                    created_at=local_start - timedelta(seconds=1)),
            Message(session_id=old.id, role="user", content="Today's lecture",
                    created_at=local_start + timedelta(seconds=1)),
        ])
        await db.commit()
        block = await today_across_sessions_for_context(1, db, "ultron", current.id)
        assert "Today's lecture" in block
        assert "Yesterday's lecture" not in block


async def test_today_continuity_recovers_relevant_older_message(sessions):
    local_start = datetime.combine(datetime.now(owner_tz()).date(), datetime.min.time(),
                                   tzinfo=owner_tz()).astimezone(timezone.utc)
    async with sessions() as db:
        old = Session(user_id=1, agent_id="ultron", triggered_by="user", model_used="test")
        current = Session(user_id=1, agent_id="ultron", triggered_by="user", model_used="test")
        db.add_all([old, current])
        await db.flush()
        db.add(Message(session_id=old.id, role="user",
                       content="ATA101 sınavı cuma günü olacak.",
                       created_at=local_start + timedelta(hours=1)))
        for index in range(32):
            db.add(Message(session_id=old.id, role="user",
                           content=f"Unrelated chat item {index}",
                           created_at=local_start + timedelta(hours=2, minutes=index)))
        await db.commit()
        block = await today_across_sessions_for_context(
            1, db, "ultron", current.id, query="ATA101 sınav ne zaman?",
        )
        assert "ATA101 sınavı cuma" in block
        assert len(block) < 3100


async def test_related_facts_use_source_links_without_inventing_causation(sessions):
    async with sessions() as db:
        root = Observation(user_id=1, observer="ultron", content="ATA101 exam moved to Friday",
                           level="explicit", subject="owner", domain="event", message_ids=[42])
        person = Observation(user_id=1, observer="ultron", content="Bedirhan studies theology",
                             level="explicit", subject="person:Bedirhan", domain="biography",
                             message_ids=[42])
        unrelated = Observation(user_id=1, observer="ultron", content="Another person studies history",
                                level="explicit", subject="person:Selim", domain="biography",
                                message_ids=[99])
        private = Observation(user_id=2, observer="ultron", content="Private same message",
                              level="explicit", subject="person:Bedirhan", domain="biography",
                              message_ids=[42])
        db.add_all([root, person, unrelated, private])
        await db.commit()
        related = await related_observations(db, user_id=1, observation_id=root.id)
        assert related["root"].id == root.id
        assert [(row.id, reasons) for row, reasons in related["links"]] == [
            (person.id, ["same owner message"]),
        ]
        assert (await related_observations(db, user_id=1, observation_id=private.id))["root"] is None
        result = await SearchMemorySkill().execute(
            {"mode": "related", "observation_id": root.id},
            SimpleNamespace(db=db, user_id=1),
        )
        assert "same owner message" in result
        assert "Bedirhan studies theology" in result
        assert "Another person" not in result and "Private same message" not in result


async def test_related_facts_connect_person_mentioned_in_separate_event(sessions):
    async with sessions() as db:
        event = Observation(user_id=1, observer="speda", content="Bedirhan moved into the dorm",
                            level="explicit", subject="owner", domain="event", message_ids=[11])
        person = Observation(user_id=1, observer="ultron", content="Bedirhan studies theology",
                             level="explicit", subject="person:Bedirhan", domain="biography",
                             message_ids=[22])
        db.add_all([event, person])
        await db.commit()
        related = await related_observations(db, user_id=1, observation_id=event.id)
        assert [(row.id, reasons) for row, reasons in related["links"]] == [
            (person.id, ["mentions subject"]),
        ]


async def test_relevant_recall_expands_linked_fact_within_existing_budget(sessions, monkeypatch):
    async with sessions() as db:
        event = Observation(user_id=1, observer="speda", content="Bedirhan moved into the dorm",
                            level="explicit", subject="owner", domain="event", message_ids=[11])
        person = Observation(user_id=1, observer="ultron", content="Bedirhan studies theology",
                             level="explicit", subject="person:Bedirhan", domain="biography",
                             message_ids=[22])
        db.add_all([event, person])
        await db.commit()

        async def search(*_args, **_kwargs):
            return [(event, 1.0)]

        monkeypatch.setattr("app.services.observations.search_observations", search)
        block = await facts_for_query(1, db, "What happened with Bedirhan?")
        assert "Bedirhan moved into the dorm" in block
        assert "Bedirhan studies theology" in block
        assert "Related to id:" in block and "mentions subject" in block


async def test_manual_observation_pins_verified_message_id(sessions, monkeypatch):
    from app.services import observations as observation_service

    async def accept(*_args, **_kwargs):
        return {"allow": True}

    async def no_embedding(_content):
        return None

    monkeypatch.setattr("app.skills.observations.ask_json", accept)
    monkeypatch.setattr(observation_service, "_embed_content", no_embedding)
    async with sessions() as db:
        session = Session(user_id=1, agent_id="ultron", triggered_by="user", model_used="test")
        db.add(session)
        await db.flush()
        message = Message(session_id=session.id, role="user",
                          content="My roommate Bedirhan studies theology.")
        db.add(message)
        await db.commit()
        context = SimpleNamespace(db=db, user_id=1, session_id=session.id,
                                  agent_id="ultron", model="test", request_id="r1")
        result = await RecordObservationSkill().execute({"observations": [{
            "content": "The owner's roommate Bedirhan studies theology.", "level": "explicit",
            "domain": "biography", "subject": "person:Bedirhan",
            "evidence": [{"ref": "message:latest", "quote": "Bedirhan studies theology."}],
            "_verified_message_ids": [999],
        }]}, context)
        assert "Recorded 1 observation" in result
        row = (await db.execute(__import__("sqlalchemy").select(Observation))).scalar_one()
        assert row.message_ids == [message.id]
