import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
from uuid import uuid4

# Test imports must never inherit a real database or provider credential.
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + str(Path(tempfile.gettempdir()) / f"speda-import-{uuid4().hex}.db")
os.environ["ANTHROPIC_API_KEY"] = "not-set"
for provider_name in ("OPENAI", "GEMINI", "ZAI", "DEEPSEEK", "NVIDIA", "KIMI", "MOONSHOT", "OLLAMA"):
    os.environ[provider_name + "_API_KEY"] = ""
os.environ["LLM_MAIN_MODEL"] = ""
os.environ["LLM_BACKGROUND_MODEL"] = ""
os.environ["VERTEX_PROJECT_ID"] = ""
os.environ["EXTERNAL_INTEGRATIONS_ENABLED"] = "false"
os.environ["MEMORY_CAPTURE_ENABLED"] = "false"
os.environ["SPEDA_API_KEY"] = "test-only-key"
os.environ["N8N_SECRET"] = "test-only-n8n"
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ['SPEDA_DATA_DIR'] = str(Path(tempfile.gettempdir()) / f'speda-test-state-{uuid4().hex}')
for external_key in ('N8N_API_KEY','TELEGRAM_BOT_TOKEN','TELEGRAM_BOT_TOKENS','AZURE_SPEECH_KEY','ELEVENLABS_API_KEY'):
    os.environ[external_key]='{}' if external_key=='TELEGRAM_BOT_TOKENS' else ''

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.context import AgentContext
from app.database import Base
import app.models
from app.models.user import User
from app.schemas.memory_runtime import EvidenceSection
from app.services.memory_store import MemoryStore, capture_source, init_indexes


def context(query="", user_id=1, **kwargs):
    return AgentContext(user_id=user_id, session_id=0, request_id=uuid4().hex,
        triggered_by="user", trigger_payload={}, output_mode="respond", model="test-model",
        system_prompt="", conversation_history=[{"role": "user", "content": query}],
        db=None, timezone="Europe/Istanbul", **kwargs)


@pytest_asyncio.fixture
async def memory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'memory.db'}")
    @event.listens_for(engine.sync_engine, "connect")
    def options(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=100")
        conn.execute("PRAGMA journal_mode=WAL")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await init_indexes(conn)
    async with sessions() as db, db.begin():
        db.add_all([User(id=1, name="Owner", timezone="Europe/Istanbul"),
                    User(id=2, name="Other owner", timezone="UTC")])
    store = MemoryStore(sessions)
    async def seed(body, *, role="canonical", owner=1, title="Ilayda", aliases=("İlayda",),
                   key=None, kind="person", section_kwargs=None, publish=True):
        async with sessions() as db, db.begin():
            source = await capture_source(db, user_id=owner, source_key=f"test:{uuid4()}",
                                          content=body, source_role=role)
            source_id = source.id
        section_text = body if publish else body[:8000]
        section = EvidenceSection(source_id=source_id, start=0, end=len(section_text), text=section_text,
            attribution={"canonical":"canonical_record", "owner":"owner_statement",
                         "quoted":"quoted_material"}[role], **(section_kwargs or {}))
        if publish:
            await store.publish(context(user_id=owner), stable_key=key or f"source:{source_id}",
                kind=kind, title=title, aliases=aliases, sections=[section])
        return source_id, section
    yield SimpleNamespace(engine=engine, sessions=sessions, store=store, seed=seed, context=context)
    await engine.dispose()
