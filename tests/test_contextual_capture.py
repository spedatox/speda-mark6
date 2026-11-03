import json

from sqlalchemy import select

from app.core.session_manager import SessionManager
from app.models.memory_serving import MemoryChange
from app.models.session import Session
from app.services.memory_prepare import MemoryPreparer
from app.services.memory_runtime import MemoryRuntime, packet_json
from tests.test_capture import FakeCaptureClient, FakeProfile


async def test_contextual_yes_keeps_assistant_question_as_context_not_owner_fact(memory):
    async with memory.sessions() as db, db.begin():
        db.add(Session(id=1,user_id=1,agent_id="speda",triggered_by="user",model_used="test"))
    sessions = SessionManager()
    async with memory.sessions() as db:
        await sessions.save_message(db,1,"assistant","Did Nora cancel the university visit?")
        await sessions.save_message(db,1,"user","Yes.")
    raw = await MemoryRuntime(memory.store).prepare(memory.context("Nora visit"))
    assert "Did Nora cancel" in packet_json(raw)
    assert "context_only_not_owner_evidence" in packet_json(raw)
    client = FakeCaptureClient([(json.dumps([{"quote":"Yes.","topic":"Nora cancelled visit"}]),"end_turn")])
    await MemoryPreparer(memory.store,client,FakeProfile()).drain(memory.context(),limit=1)
    prepared = await MemoryRuntime(memory.store).prepare(memory.context("Nora visit"))
    assert "Did Nora cancel" in packet_json(prepared)
    assert '"text":"Yes."' in packet_json(prepared)
    assert "owner_statement" in packet_json(prepared)


async def test_capture_has_explicit_disposition_for_each_completed_chunk(memory):
    source_id, _ = await memory.seed("A" * 8500,role="owner",publish=False)
    worker = MemoryPreparer(memory.store,FakeCaptureClient([("[]","end_turn"),("[]","end_turn")]),FakeProfile())
    await worker.drain(memory.context(),limit=2)
    async with memory.sessions() as db:
        source = await db.get(MemoryChange,source_id)
        assert source.capture_offset == 8500
        assert source.provenance["capture_dispositions"] == [
            {"start":0,"end":6000,"outcome":"valid_empty"},
            {"start":6000,"end":8500,"outcome":"valid_empty"}]
