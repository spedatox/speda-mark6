# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A confirmation turn must not invalidate an earlier owner's evidence."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models
from app.database import Base
from app.models.memory_file import MemoryFile
from app.models.memory_write_receipt import MemoryWriteReceipt
from app.models.message import Message
from app.models.session import Session
from app.services.memory_admission import resolve_evidence
from app.skills.memory_write import RegistryUpsertSkill


@pytest.fixture
async def sessions():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_registry_retry_cites_original_owner_message(sessions, monkeypatch):
    reviewer = AsyncMock(return_value="The owner's message supports the profile.")
    monkeypatch.setattr("app.services.memory_admission.admit", reviewer)
    quote = "Adı bedirhan 2007 li, Ankara ilahiyat öğrencisi, Kahramanmaraşlı."
    async with sessions() as db:
        session = Session(user_id=1, agent_id="speda", triggered_by="user", model_used="test")
        db.add(session)
        await db.flush()
        original = Message(session_id=session.id, role="user", content="Yusuf ile oda arkadaşıyız. " + quote)
        db.add(original)
        await db.flush()
        db.add(Message(session_id=session.id, role="user", content="tekrar dene bakim"))
        await db.commit()

        result = await RegistryUpsertSkill().execute({
            "kind": "person", "entity": "Bedirhan", "category": "Personal",
            "who": "Ahmet'in Ankara'daki oda arkadaşı; 2007 doğumlu, Kahramanmaraşlı ve Ankara'da ilahiyat öğrencisi.",
            "evidence": [{"ref": "message:latest", "quote": quote}],
        }, SimpleNamespace(db=db, user_id=1, session_id=session.id, agent_id="speda",
                           request_id="retry-bed-1", model="test"))

        from app.services.memory_paths import MonthPeriod
        path = f"/memories/social/{MonthPeriod.current().folder_name}/personal/bedirhan.md"
        assert result.startswith("Written to " + path)
        file = (await db.execute(select(MemoryFile).where(MemoryFile.path == path))).scalar_one()
        receipt = (await db.execute(select(MemoryWriteReceipt))).scalar_one()
        assert "Kahramanmaraşlı" in file.content
        assert receipt.evidence[0]["ref"] == f"message:{original.id}"
        assert reviewer.await_count == 1


async def test_retry_evidence_stays_in_same_owner_session(sessions):
    quote = "Adı Bedirhan, Ankara ilahiyat öğrencisi."
    async with sessions() as db:
        active = Session(user_id=1, agent_id="speda", triggered_by="user", model_used="test")
        other = Session(user_id=2, agent_id="speda", triggered_by="user", model_used="test")
        automated = Session(user_id=1, agent_id="orion", triggered_by="n8n", model_used="test")
        db.add_all([active, other, automated])
        await db.flush()
        db.add_all([
            Message(session_id=active.id, role="user", content="tekrar dene bakim"),
            Message(session_id=other.id, role="user", content=quote),
            Message(session_id=automated.id, role="user", content=quote),
            Message(session_id=active.id, role="assistant", content=quote),
        ])
        await db.commit()
        with pytest.raises(ValueError, match="Quotation is not present"):
            await resolve_evidence(db, 1, [{"ref": "message:latest", "quote": quote}], session_id=active.id)


async def test_retry_fallback_requires_literal_prior_quote(sessions):
    async with sessions() as db:
        session = Session(user_id=1, agent_id="speda", triggered_by="user", model_used="test")
        db.add(session)
        await db.flush()
        db.add_all([
            Message(session_id=session.id, role="user", content="Bedirhan is from Kahramanmaraş."),
            Message(session_id=session.id, role="user", content="tekrar dene bakim"),
        ])
        await db.commit()
        with pytest.raises(ValueError, match="Quotation is not present"):
            await resolve_evidence(db, 1, [{"ref": "message:latest", "quote": "Bedirhan is from Kahramanmaras."}], session_id=session.id)


@pytest.mark.parametrize("failure,retryable", [(TimeoutError("provider timed out"), True), (ValueError("invalid publisher model"), False)])
async def test_event_intake_survives_failure_and_only_retries_transient_unit(sessions, monkeypatch, failure, retryable):
    from app.skills.memory_event import MemoryEventSkill
    from app.models.memory_capture_job import MemoryCaptureJob
    from app.models.memory_capture_payload import MemoryCapturePayload
    reviewer = AsyncMock(side_effect=failure)
    monkeypatch.setattr("app.services.memory_admission.admit", reviewer)
    async with sessions() as db:
        session = Session(user_id=1, agent_id="speda", triggered_by="user", model_used="test")
        db.add(session)
        await db.flush()
        message = Message(session_id=session.id, role="user", content="Bedirhan and I went to the cinema on 2026-09-28.")
        db.add(message)
        await db.commit()
        ctx = SimpleNamespace(db=db, user_id=1, session_id=session.id, agent_id="speda", request_id="event-test", model="test", timezone="Europe/Istanbul", trigger_payload={})
        args = {"summary": message.content, "title": "Cinema visit", "date": "2026-09-28", "source_idempotency_key": "cinema-1", "evidence": [{"ref": f"message:{message.id}", "quote": message.content}]}
        first = await MemoryEventSkill().execute(args, ctx)
        assert ("retryable_failure" if retryable else "needs_review") in first
        job = (await db.execute(select(MemoryCaptureJob).execution_options(populate_existing=True))).scalar_one()
        ident = job.candidate_id
        payload = await db.get(MemoryCapturePayload, job.id)
        assert payload.payload["summary"] == args["summary"] and job.attempts == 1
        assert not (await db.execute(select(MemoryFile))).scalars().all()
        reviewer.side_effect = None
        reviewer.return_value = "Supported."
        second = await MemoryEventSkill().execute(args, ctx)
        if retryable:
            import json
            assert json.loads(second)["record_id"] == ident
            third = await MemoryEventSkill().execute(args, ctx)
            assert json.loads(third)["already_recorded"]
            assert reviewer.await_count == 2
            assert len((await db.execute(select(MemoryFile))).scalars().all()) == 1
        else:
            assert "needs_review" in second and reviewer.await_count == 1
        # A reused external key must never silently swallow different content.
        collision = await MemoryEventSkill().execute({**args, "summary": "A different event"}, ctx)
        assert "different capture" in collision


async def test_event_retry_budget_stops_after_three_provider_calls(sessions, monkeypatch):
    from app.skills.memory_event import MemoryEventSkill
    from app.models.memory_capture_job import MemoryCaptureJob
    reviewer = AsyncMock(side_effect=TimeoutError("unavailable"))
    monkeypatch.setattr("app.services.memory_admission.admit", reviewer)
    async with sessions() as db:
        session = Session(user_id=1, agent_id="speda", triggered_by="user", model_used="test")
        db.add(session)
        await db.flush()
        msg = Message(session_id=session.id, role="user", content="I visited the cinema.")
        db.add(msg)
        await db.commit()
        ctx = SimpleNamespace(db=db, user_id=1, session_id=session.id, agent_id="speda", request_id="timeout", model="test", timezone="Europe/Istanbul", trigger_payload={})
        args = {"summary": msg.content, "evidence": [{"ref": f"message:{msg.id}", "quote": msg.content}]}
        for _ in range(3):
            assert "retryable_failure" in await MemoryEventSkill().execute(args, ctx)
        assert "budget exhausted" in await MemoryEventSkill().execute(args, ctx)
        assert reviewer.await_count == 3
        job = (await db.execute(select(MemoryCaptureJob).execution_options(populate_existing=True))).scalar_one()
        assert job.attempts == 3


async def test_event_links_all_known_participants_without_duplicate_identities(sessions, monkeypatch):
    from app.models.memory_entity import MemoryEntity
    from app.models.memory_graph_edge import MemoryGraphEdge
    from app.skills.memory_event import MemoryEventSkill
    import json
    monkeypatch.setattr("app.services.memory_admission.admit", AsyncMock(return_value="Supported."))
    async with sessions() as db:
        session=Session(user_id=1,agent_id="speda",triggered_by="user",model_used="test")
        db.add(session)
        await db.flush()
        msg=Message(session_id=session.id,role="user",content="I visited the cinema with Bedirhan and Yusuf.")
        db.add(msg)
        for ident,name in (("bed","Bedirhan"),("yus","Yusuf")):
            db.add(MemoryEntity(id=ident,user_id=1,category="social",entity_type="person",canonical_name=name,aliases=[]))
        await db.commit()
        ctx=SimpleNamespace(db=db,user_id=1,session_id=session.id,agent_id="speda",request_id="participants",model="test",timezone="Europe/Istanbul",trigger_payload={})
        result=json.loads(await MemoryEventSkill().execute({"summary":msg.content,"title":"Cinema","related_entities":["Bedirhan","Yusuf"],"evidence":[{"ref":f"message:{msg.id}","quote":msg.content}]},ctx))
        edges=(await db.execute(select(MemoryGraphEdge).where(MemoryGraphEdge.source_ref=="record:"+result["record_id"],MemoryGraphEdge.relation_type=="mentions"))).scalars().all()
        assert {edge.target_ref for edge in edges}=={"entity:bed","entity:yus"}
        assert len((await db.execute(select(MemoryEntity))).scalars().all())==2
