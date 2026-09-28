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

        assert result.startswith("Written to /memories/social/personal/bedirhan.md")
        file = (await db.execute(select(MemoryFile).where(MemoryFile.path == "/memories/social/personal/bedirhan.md"))).scalar_one()
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
