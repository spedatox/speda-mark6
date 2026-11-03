import asyncio
from datetime import date, timedelta
import json

import pytest
from sqlalchemy import select

from app.models.memory_serving import MemoryChange
from app.schemas.memory_runtime import EvidenceSection, token_upper_bound
from app.services.memory_runtime import MemoryRuntime, packet_json, query_terms
from app.services.memory_store import MemoryStore, StalePublication, capture_source, exact_sections
from app.skills.memory_lookup import MemoryLookupSkill


async def test_person_identity_and_relationship_in_first_packet(memory):
    body = "İlayda is the owner's sister; her husband is Erdem. The owner described their relationship as strained."
    await memory.seed(body)
    await memory.seed("The perfect square lesson covered equations.", title="Mathematics", aliases=(), kind="course")
    packet = await MemoryRuntime(memory.store).prepare(memory.context("Ilayda and Erdem are visiting."))
    assert body in packet_json(packet)
    assert "perfect square" not in packet_json(packet)
    assert packet.unit_revisions


async def test_recent_cross_chat_correction_available_without_extraction(memory):
    runtime = MemoryRuntime(memory.store)
    await memory.seed("Nora was planning a summer visit.", title="Nora", aliases=("Nora",))
    await runtime.prepare(memory.context("Nora visit"))
    await memory.seed("Correction: Nora cancelled the visit; she is not coming.", role="owner", publish=False)
    packet = await runtime.prepare(memory.context("Is Nora visiting?"))
    assert "cancelled" in packet_json(packet)
    assert "owner_statement" in packet_json(packet)


async def test_owner_scope_and_revocation_across_runtime_caches(memory):
    source_id, _ = await memory.seed("Nora has an owner-private boundary.", title="Nora", aliases=("Nora",))
    await memory.seed("Nora has another owner's private history.", owner=2, title="Nora", aliases=("Nora",))
    other_store = MemoryStore(memory.sessions)
    runtime = MemoryRuntime(other_store)
    initial = await runtime.prepare(memory.context("Nora"))
    assert "owner-private" in packet_json(initial)
    assert "another owner's" not in packet_json(initial)
    await memory.store.revoke(memory.context(), source_id)
    after = await runtime.prepare(memory.context("Nora"))
    assert "owner-private" not in packet_json(after)
    assert after.publication_epoch > initial.publication_epoch


async def test_ambiguous_alias_is_not_merged(memory):
    await memory.seed("Alex Green is a colleague.", title="Alex Green", aliases=("Alex",))
    await memory.seed("Alex White is a cousin.", title="Alex White", aliases=("Alex",))
    packet = await MemoryRuntime(memory.store).prepare(memory.context("Alex"))
    assert packet.status == "ambiguous"
    assert packet.ambiguities


async def test_one_lookup_batch_shared_across_registry_calls(memory):
    await memory.seed("Nora enjoys hiking.", title="Nora", aliases=("Nora",))
    runtime = MemoryRuntime(memory.store)
    skill = MemoryLookupSkill(runtime)
    ctx = memory.context("Nora")
    first = json.loads(await skill.execute({"query": "Nora"}, ctx))
    second = json.loads(await skill.execute({"query": "Nora hiking"}, ctx))
    assert first["evidence"]
    assert not second["evidence"]
    assert ctx.memory_budget.lookup_batches == 1


async def test_timeout_cancels_read_instead_of_leaving_work_running(memory):
    cancelled = asyncio.Event()
    async def slow(*args, **kwargs):
        try:
            await asyncio.sleep(2)
        finally:
            cancelled.set()
    memory.store.read = slow
    runtime = MemoryRuntime(memory.store, lookup_timeout=0.015)
    packet = await runtime.lookup(memory.context(), "Nora")
    assert packet.status == "deadline"
    assert cancelled.is_set()
    assert packet.elapsed_ms < 500


async def test_publication_cas_and_idempotency(memory):
    _, first = await memory.seed("Nora prefers quiet evenings.", title="Nora", key="person:nora")
    ctx = memory.context()
    assert await memory.store.publish(ctx, stable_key="person:nora", kind="person", title="Nora",
        sections=[first], aliases=("İlayda",), expected_revision=0) == 1
    with pytest.raises(StalePublication):
        await memory.store.publish(ctx, stable_key="person:nora", kind="person", title="Changed",
            sections=[first], expected_revision=0)
    assert await memory.store.publish(ctx, stable_key="person:nora", kind="person", title="Nora update",
        sections=[first], expected_revision=1) == 2


async def test_forged_quote_and_cross_owner_evidence_rejected(memory):
    _, section = await memory.seed("Nora said she might visit.")
    for bad in [section.model_copy(update={"text": "Nora definitely visited."}),
                section.model_copy(update={"attribution": "owner_statement"})]:
        with pytest.raises(ValueError):
            await memory.store.publish(memory.context(), stable_key="bad", kind="person", title="Nora", sections=[bad])
    with pytest.raises(ValueError):
        await memory.store.publish(memory.context(user_id=2), stable_key="bad", kind="person", title="Nora", sections=[section])


async def test_immutable_source_key_cannot_be_overwritten(memory):
    async with memory.sessions() as db, db.begin():
        await capture_source(db, user_id=1, source_key="same", content="The original.")
    with pytest.raises(ValueError):
        async with memory.sessions() as db, db.begin():
            await capture_source(db, user_id=1, source_key="same", content="Replacement.")


async def test_future_validity_not_injected_as_current(memory):
    await memory.seed("Nora starts the role next year.", title="Nora", aliases=("Nora",),
        section_kwargs={"valid_from": date.today() + timedelta(days=365)})
    packet = await MemoryRuntime(memory.store).prepare(memory.context("Nora role"))
    assert packet.status not in {"unavailable", "deadline"}
    assert "starts the role" not in packet_json(packet)


async def test_expired_review_is_unverified_not_completed(memory):
    await memory.seed("Nora is waiting for an application decision.", title="Nora", aliases=("Nora",),
        section_kwargs={"review_on": date.today() - timedelta(days=1)})
    packet = await MemoryRuntime(memory.store).prepare(memory.context("Nora decision"))
    assert "current status unverified" in packet_json(packet)
    assert "completed" not in packet_json(packet)


async def test_memory_result_respects_one_combined_budget(memory):
    for index in range(12):
        await memory.seed(f"Nora event {index}: " + "careful context " * 60,
                          title="Nora", aliases=("Nora",))
    runtime = MemoryRuntime(memory.store)
    ctx = memory.context("Nora")
    first = await runtime.prepare(ctx)
    second = await runtime.lookup(ctx, "Nora")
    assert token_upper_bound(packet_json(first)) <= ctx.memory_budget.automatic_limit
    assert token_upper_bound(packet_json(second)) <= ctx.memory_budget.lookup_limit
    assert ctx.memory_budget.used <= ctx.memory_budget.turn_limit
    assert set(first.evidence_handles).isdisjoint(second.evidence_handles)


async def test_large_source_sections_remain_addressable_and_marked(memory):
    source_id, _ = await memory.seed("Nora " + "x" * 4000, publish=False)
    async with memory.sessions() as db:
        source = await db.get(MemoryChange, source_id)
        sections = exact_sections(source, max_chars=1000)
    assert "".join(s.text for s in sections) == source.content
    assert all(s.fragment for s in sections)
    assert sections[-1].end == len(source.content)


def test_generic_adjectives_are_not_identity_anchors():
    assert "perfect" not in query_terms("Mister and mrs perfect all-mighty. Great.")
    assert "ilayda" in query_terms("İlayda")


async def test_long_biography_cannot_evict_focused_person(memory):
    await memory.seed("The owner's old career history. " * 170,kind="core",title="Owner",aliases=())
    await memory.seed("**Who:** Nora is the owner's sister. Their relationship is strained.",
                      title="Nora Green",aliases=())
    packet=await MemoryRuntime(memory.store).prepare(memory.context("Nora is coming over."))
    assert "owner's sister" in packet_json(packet)
    assert packet.unit_revisions
