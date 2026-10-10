"""Debug export preserves raw history and full audit data rather than UI previews."""
import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base
from app.models.user import User
from app.models.project import Project
from app.models.session import Session
from app.models.message import Message
from app.models.tool_call import ToolCall
from app.models.worker_execution import WorkerExecution, WorkerEvent, WorkerInput, WorkerCompletion
from app.routers.chat import export_session
from app.services.chat_history import debug_export


async def test_export_preserves_all_records_and_long_results():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync: Base.metadata.create_all(
                sync, tables=[User.__table__, Project.__table__, Session.__table__, Message.__table__, ToolCall.__table__,
                              WorkerExecution.__table__, WorkerEvent.__table__, WorkerInput.__table__, WorkerCompletion.__table__]
            ))
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            db.add(User(id=1, name="Owner"))
            db.add_all([
                Session(id=1, user_id=1, agent_id="speda", triggered_by="user", model_used="test", summary="compressed", summary_through_id=2),
                Session(id=2, user_id=1, agent_id="sentinel", triggered_by="user", model_used="test"),
            ])
            full = {"text": "ş" * 12000, "nested": {"answer": [1, 2]}}
            stamp = datetime(2026, 10, 1, 12)
            content = [{"type": "tool_result", "tool_use_id": "call-1", "content": "raw result"}]
            db.add_all([
                Message(id=2, session_id=1, role="tool_result", content=content, created_at=stamp),
                Message(id=1, session_id=1, role="user", content="Merhaba", created_at=stamp),
                Message(id=3, session_id=2, role="assistant", content="other session", created_at=stamp),
                ToolCall(session_id=1, request_id="request-1", tool_name="fetch", tool_input={"url": "example"}, tool_result=full, duration_ms=42),
                ToolCall(session_id=1, request_id="request-2", tool_name="fetch", tool_input={}, tool_result="Error: failed", error="Error: failed"),
                ToolCall(session_id=2, request_id="other", tool_name="other", tool_input={}),
            ])
            await db.commit()
            payload = json.loads(json.dumps(await debug_export(db, 1), ensure_ascii=False))
            assert payload["session"]["summary_through_id"] == 2
            assert [m["id"] for m in payload["messages"]] == [1, 2]
            assert payload["messages"][1]["content"] == content
            assert payload["messages"][0]["created_at"].endswith("+00:00")
            assert len(payload["tool_calls"]) == 2
            assert payload["tool_calls"][0]["tool_result"] == full
            assert payload["tool_calls"][0]["duration_ms"] == 42
            assert payload["tool_calls"][1]["error"] == "Error: failed"
            assert await debug_export(db, 999) is None
    finally:
        await engine.dispose()


async def test_export_refuses_incomplete_running_turn():
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        turns=SimpleNamespace(active=lambda **kwargs: [{"request_id": "running"}])
    )))
    with pytest.raises(HTTPException) as error:
        await export_session(1, request, None)
    assert error.value.status_code == 409


async def test_export_missing_session_returns_404():
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        turns=SimpleNamespace(active=lambda **kwargs: [])
    )))

    class MissingDB:
        async def get(self, *args):
            return None

    with pytest.raises(HTTPException) as error:
        await export_session(999, request, MissingDB())
    assert error.value.status_code == 404
