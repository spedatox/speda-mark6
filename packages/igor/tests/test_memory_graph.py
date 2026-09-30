"""The memory graph connects durable facts, events, documents and evidence."""

import json
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models
from app.database import Base
from app.models.memory_capture_job import MemoryCaptureJob
from app.models.memory_file import MemoryFile
from app.models.memory_graph_edge import MemoryGraphEdge
from app.models.memory_record_meta import MemoryRecordMeta
from app.models.memory_revision import MemoryRevision
from app.models.memory_write_receipt import MemoryWriteReceipt
from app.models.message import Message
from app.models.observation import Observation
from app.models.session import Session
from app.services.memory_graph import build_context, index_observation, rebuild_graph_for_user
from app.services.memory_states import version
from app.services.observations import record_observations, related_observations
from app.skills.memory_event import MemoryEventSkill


@pytest.fixture
async def sessions():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_one_source_connects_person_event_and_document(sessions, monkeypatch):
    async def accept(*_args, **_kwargs):
        return "supported"

    async def no_embedding(_content):
        return None

    monkeypatch.setattr("app.services.memory_admission.admit", accept)
    monkeypatch.setattr("app.services.observations._embed_content", no_embedding)
    async with sessions() as db:
        session = Session(user_id=1, agent_id="speda", triggered_by="user", model_used="test")
        db.add(session)
        await db.flush()
        message = Message(session_id=session.id, role="user",
                          content="Bedirhan moved into my room in Ankara today.")
        db.add(message)
        await db.commit()
        facts, refused = await record_observations(
            db, user_id=1, observer="speda", session_id=session.id,
            message_ids=[message.id], proposals=[{
                "content": "The owner's roommate Bedirhan moved into the Ankara dorm.",
                "level": "explicit", "subject": "person:Bedirhan", "domain": "event",
            }],
        )
        assert not refused
        context = SimpleNamespace(db=db, user_id=1, session_id=session.id,
                                  request_id="same-turn", agent_id="speda", model="test",
                                  trigger_payload={}, timezone="Europe/Istanbul")
        args = {"summary": "Bedirhan moved into the owner's Ankara dorm room.",
                "title": "Bedirhan moved in", "date": date.today().isoformat(),
                "evidence": [{"ref": "message:latest", "quote": "Bedirhan moved into my room"}]}
        result = json.loads(await MemoryEventSkill().execute(args, context))
        assert result["record_id"]
        record = result["record_id"]
        related = await build_context(db, 1, f"observation:{facts[0].id}", depth=3)
        assert f"record:{record}" in related
        assert "evidenced_by" in related
        assert "Bedirhan" in related

    # A fresh DB session proves metadata was committed by the capture itself,
    # not accidentally by the orchestrator's later tool-call logging.
    async with sessions() as db:
        meta = (await db.execute(select(MemoryRecordMeta).where(
            MemoryRecordMeta.record_id == record))).scalar_one()
        assert meta.user_id == 1
        job = (await db.execute(select(MemoryCaptureJob))).scalar_one()
        assert job.state == "committed" and job.committed_record_ids == [record]
        assert (await db.execute(select(MemoryGraphEdge))).scalars().all()
        assert "No accessible" == (await build_context(db, 2, f"record:{record}"))[:13]


async def test_event_retry_reuses_id_and_distinct_events_in_one_turn_do_not_collide(sessions, monkeypatch):
    async def accept(*_args, **_kwargs):
        return "supported"

    monkeypatch.setattr("app.services.memory_admission.admit", accept)
    async with sessions() as db:
        session = Session(user_id=1, agent_id="speda", triggered_by="user", model_used="test")
        db.add(session)
        await db.flush()
        db.add(Message(session_id=session.id, role="user",
                       content="I went to Ankara and visited the museum."))
        await db.commit()
        context = SimpleNamespace(db=db, user_id=1, session_id=session.id,
                                  request_id="one-turn", agent_id="speda", model="test",
                                  trigger_payload={}, timezone="Europe/Istanbul")
        skill = MemoryEventSkill()
        first = {"summary": "The owner went to Ankara.", "title": "Ankara trip",
                 "date": "2026-09-30",
                 "evidence": [{"ref": "message:latest", "quote": "I went to Ankara"}]}
        second = {"summary": "The owner visited the museum in Ankara.",
                  "title": "Museum visit", "date": "2026-09-30",
                  "evidence": [{"ref": "message:latest", "quote": "visited the museum"}]}
        one = json.loads(await skill.execute(first, context))
        retry = json.loads(await skill.execute(first, context))
        two = json.loads(await skill.execute(second, context))
        assert retry["record_id"] == one["record_id"]
        assert retry["path"] == one["path"] and retry["already_recorded"]
        assert two["record_id"] != one["record_id"]
        assert len((await db.execute(select(MemoryRecordMeta))).scalars().all()) == 2
        assert len((await db.execute(select(MemoryFile))).scalars().all()) == 2


async def test_legacy_graph_backfill_is_resumable_and_preserves_owner_boundary(sessions, monkeypatch):
    monkeypatch.setattr("app.database.AsyncSessionLocal", sessions)
    async with sessions() as db:
        session = Session(user_id=1, agent_id="speda", triggered_by="user", model_used="test")
        db.add(session)
        await db.flush()
        message = Message(session_id=session.id, role="user", content="Bedirhan joined my room.")
        db.add(message)
        await db.flush()
        obs = Observation(user_id=1, observer="speda", content="Bedirhan joined the room.",
                          level="explicit", subject="person:Bedirhan", domain="event",
                          message_ids=[message.id])
        db.add(obs)
        path = "/memories/social/personal/bedirhan.md"
        content = "# Bedirhan\n\nJoined the owner's room."
        db.add(MemoryFile(user_id=1, path=path, content=content))
        db.add(MemoryRevision(user_id=1, path=path, author="speda", action="create",
                              before="", after=content, record_id="legacy-id"))
        db.add(MemoryWriteReceipt(user_id=1, path=path, author="speda",
                                  before_hash=version(""), after_hash=version(content),
                                  evidence=[{"ref": f"message:{message.id}",
                                             "quote": "Bedirhan joined my room"}],
                                  rationale="Legacy write"))
        await db.commit()
        obs_id = obs.id

    assert await rebuild_graph_for_user(1, batch_size=1) == {"observations": 1, "revisions": 1}
    assert await rebuild_graph_for_user(1, batch_size=1) == {"observations": 0, "revisions": 0}
    async with sessions() as db:
        context = await build_context(db, 1, f"observation:{obs_id}", depth=3)
        assert "record:legacy-id" in context and path in context
        person_context = await build_context(db, 1, "entity:person:Bedirhan", depth=2)
        assert path in person_context and f"observation:{obs_id}" in person_context
        assert "No accessible" in await build_context(db, 2, "record:legacy-id")


async def test_related_fact_survives_more_than_500_unrelated_observations(sessions):
    async with sessions() as db:
        session = Session(user_id=1, agent_id="speda", triggered_by="user", model_used="test")
        db.add(session)
        await db.flush()
        message = Message(session_id=session.id, role="user", content="Bedirhan is my roommate.")
        db.add(message)
        await db.flush()
        event = Observation(user_id=1, observer="speda", content="Bedirhan became my roommate.",
                            level="explicit", subject="owner", domain="event",
                            message_ids=[message.id])
        person = Observation(user_id=1, observer="speda", content="Bedirhan is the owner's roommate.",
                             level="explicit", subject="person:Bedirhan", domain="state",
                             message_ids=[message.id])
        db.add_all([event, person])
        await db.flush()
        await index_observation(db, 1, event)
        await index_observation(db, 1, person)
        db.add_all([
            Observation(user_id=1, observer="speda", content=f"Unrelated fact {i}.",
                        level="explicit", subject="owner", domain="state")
            for i in range(550)
        ])
        await db.commit()
        linked = await related_observations(db, user_id=1, observation_id=event.id)
        assert [row.id for row, _ in linked["links"]] == [person.id]
