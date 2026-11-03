import asyncio
from time import monotonic

import pytest
from sqlalchemy import text

from app.schemas.memory_runtime import Deadline
from app.services.memory_runtime import MemoryRuntime, packet_json


async def test_sqlite_vm_work_is_interrupted_at_deadline(memory):
    started = monotonic()
    with pytest.raises(Exception, match="interrupted"):
        async with memory.store.reader(Deadline.after(0.02)) as db:
            await db.execute(text("WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<100000000) SELECT sum(x) FROM n"))
    assert monotonic() - started < 0.5
    # The pool connection's progress handler must be reset after interruption.
    assert await memory.store.epoch(memory.context()) == 0


async def test_source_revoked_during_assembly_is_withheld(memory):
    source_id, _ = await memory.seed("Nora has a private concern.", title="Nora", aliases=("Nora",))
    original_read = memory.store.read
    async def read_then_revoke(*args, **kwargs):
        result = await original_read(*args, **kwargs)
        await memory.store.revoke(memory.context(), source_id)
        return result
    memory.store.read = read_then_revoke
    packet = await MemoryRuntime(memory.store).prepare(memory.context("Nora"))
    assert "private concern" not in packet_json(packet)
    assert not packet.blocks


async def test_exact_source_request_can_read_without_a_keyword_match(memory):
    source_id, _ = await memory.seed("A source with an uncommon fact.", publish=False)
    packet = await MemoryRuntime(memory.store).lookup(memory.context(), "read selected evidence", source_ids=[source_id])
    assert "uncommon fact" in packet_json(packet)


async def test_new_owner_source_does_not_invalidate_unchanged_prepared_units(memory):
    await memory.seed("Nora prefers quiet evenings.", title="Nora", aliases=("Nora",))
    epoch = await memory.store.epoch(memory.context())
    await memory.seed("A newly reported afternoon experience.", role="owner", publish=False)
    assert await memory.store.epoch(memory.context()) == epoch


async def test_same_entity_multiple_record_parts_is_not_alias_ambiguity(memory):
    from app.services.memory_store import capture_source
    from app.schemas.memory_runtime import EvidenceSection
    async with memory.sessions() as db, db.begin():
        for part in range(2):
            source = await capture_source(db, user_id=1, source_key=f"part:{part}",
                content=f"Nora account part {part}.", source_role="canonical",
                provenance={"entity_id":"same-person"})
            section = EvidenceSection(source_id=source.id,start=0,end=len(source.content),
                text=source.content,attribution="canonical_record")
            await memory.store.publish(memory.context(), stable_key=f"part:{part}",kind="person",
                title="Nora",aliases=("Nora",),sections=[section], db=db)
    packet = await MemoryRuntime(memory.store).prepare(memory.context("Nora"))
    assert not packet.ambiguities
    assert len(packet.blocks) == 2
