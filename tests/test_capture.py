from types import SimpleNamespace
import json

import pytest
from sqlalchemy import select

from app.models.memory_serving import MemoryChange, MemoryServingUnit
from app.services.memory_prepare import MemoryPreparer, evidence_from_capture, parse_capture


@pytest.mark.parametrize("raw,stop,outcome", [
    ("[]", "end_turn", "valid_empty"),
    ("nonsense", "end_turn", "invalid_output"),
    ('[{"quote":"x"', "end_turn", "invalid_output"),
    ('[{"quote":"x","topic":"Nora"}]', "max_tokens", "truncated"),
    ("{}", "end_turn", "invalid_output"),
    ("[1]", "end_turn", "invalid_output"),
    ('[{"quote":"x","topic":"Nora"}]', "end_turn", "accepted_candidates"),
    ('```json\n[]\n```', "end_turn", "valid_empty")])
def test_capture_outcomes_are_distinct(raw, stop, outcome):
    assert parse_capture(raw, stop).outcome == outcome


class FakeCaptureClient:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.calls = 0
    async def create_message(self, **kwargs):
        self.calls += 1
        raw, reason = next(self.outputs)
        return SimpleNamespace(stop_reason=reason, content=[SimpleNamespace(type="text", text=raw)])


class FakeProfile:
    def allocate_model(self, *args, **kwargs):
        return "test-background-model"


async def test_malformed_capture_preserves_offset_and_original(memory):
    source_id, _ = await memory.seed("Nora cancelled the visit.", role="owner", publish=False)
    client = FakeCaptureClient([("malformed", "end_turn")])
    worker = MemoryPreparer(memory.store, client, FakeProfile())
    await worker.drain(memory.context(), limit=1)
    async with memory.sessions() as db:
        row = await db.get(MemoryChange, source_id)
        assert row.capture_offset == 0
        assert row.outcome == "invalid_output"
        assert row.content == "Nora cancelled the visit."


async def test_named_person_and_ordinary_experience_are_preserved(memory):
    text = "Ilayda prefers to keep wedding discussions private."
    source_id, _ = await memory.seed(text, role="owner", publish=False)
    client = FakeCaptureClient([(json.dumps([{"quote": text, "topic": "Ilayda"}]), "end_turn")])
    worker = MemoryPreparer(memory.store, client, FakeProfile())
    await worker.drain(memory.context(), limit=1)
    async with memory.sessions() as db:
        row = await db.get(MemoryChange, source_id)
        assert row.capture_offset == len(text)
        assert row.outcome == "accepted"
        units = (await db.execute(select(MemoryServingUnit))).scalars().all()
        assert units[0].sections[0]["text"] == text


async def test_long_source_never_marks_omitted_tail_covered(memory):
    text = "A" * 8500 + " latest correction at the end"
    source_id, _ = await memory.seed(text, role="owner", publish=False)
    worker = MemoryPreparer(memory.store, FakeCaptureClient([("[]", "end_turn")]), FakeProfile())
    await worker.drain(memory.context(), limit=1)
    async with memory.sessions() as db:
        row = await db.get(MemoryChange, source_id)
        assert row.capture_offset == 6000
        assert row.outcome == "pending"
        assert row.content.endswith("latest correction at the end")


async def test_invalid_quote_is_not_published(memory):
    source_id, _ = await memory.seed("Nora might visit.", role="owner", publish=False)
    client = FakeCaptureClient([('[{"quote":"Nora visited.","topic":"Nora"}]', "end_turn")])
    await MemoryPreparer(memory.store, client, FakeProfile()).drain(memory.context(), limit=1)
    async with memory.sessions() as db:
        assert (await db.get(MemoryChange, source_id)).capture_offset == 0
        assert not (await db.execute(select(MemoryServingUnit))).scalars().all()


async def test_successful_candidate_reused_after_publication_failure(memory):
    text = "Nora had a difficult afternoon at university."
    await memory.seed(text, role="owner", publish=False)
    client = FakeCaptureClient([(json.dumps([{"quote": text, "topic": "Nora"}]), "end_turn")])
    worker = MemoryPreparer(memory.store, client, FakeProfile())
    actual_publish = memory.store.publish
    async def fail(*args, **kwargs):
        raise RuntimeError("Simulated commit failure")
    memory.store.publish = fail
    await worker.drain(memory.context(), limit=1)
    memory.store.publish = actual_publish
    await worker.drain(memory.context(), limit=1)
    assert client.calls == 1
    async with memory.sessions() as db:
        row = (await db.execute(select(MemoryChange))).scalar_one()
        assert row.outcome == "accepted"


async def test_valid_empty_has_explicit_coverage_and_no_facts(memory):
    source_id, _ = await memory.seed("Hello there.", role="owner", publish=False)
    await MemoryPreparer(memory.store, FakeCaptureClient([("[]", "end_turn")]), FakeProfile()).drain(memory.context(), limit=1)
    async with memory.sessions() as db:
        row = await db.get(MemoryChange, source_id)
        assert row.outcome == "valid_empty" and row.capture_offset == len(row.content)
