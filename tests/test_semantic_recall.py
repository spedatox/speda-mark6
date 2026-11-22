"""End-to-end tests for real local multilingual ONNX semantic recall, RRF/MMR, and sync lifecycle."""
import json
import numpy as np
import pytest
from sqlalchemy import select

from app.models.memory_serving import MemoryEmbedding
from app.services.memory_runtime import MemoryRuntime, packet_json
from app.services.semantic_index import SemanticIndex, mmr_order, rrf_fuse


@pytest.mark.asyncio
async def test_cross_lingual_zero_keyword_overlap_semantic_recall(memory):
    """English query with zero shared words retrieves Turkish memory via multilingual-e5-small."""
    await memory.seed(
        "Ankara'ya dönerken kendi arabam yok, Bursa Otogarı'ndan otobüs bileti alıp döneceğim.",
        title="Bursa Return Transport",
        kind="episode",
        aliases=(),
    )
    await memory.seed(
        "Unrelated note about kitchen spices and olive oil recipes.",
        title="Cooking Notes",
        kind="episode",
        aliases=(),
    )
    runtime = MemoryRuntime(memory.store)
    packet = await runtime.prepare(
        memory.context("How will I commute back without a vehicle?", agent_id="speda")
    )
    topics = [json.loads(b).get("topic", "") for b in packet.blocks if "current_state" not in json.loads(b)]
    assert "Bursa Return Transport" in topics
    assert topics.index("Bursa Return Transport") == 0


@pytest.mark.asyncio
async def test_paraphrase_midterm_scores_retrieved_without_literal_word_match(memory):
    """Query 'how did I perform on my university tests?' recalls Statistics 99 & Accounting 100."""
    await memory.seed(
        "MIS215 Statistics I result RELEASED: scored 99 out of 100.",
        title="Statistics I Result",
        kind="course",
        aliases=(),
    )
    await memory.seed(
        "MIS243 General Accounting outcome: achieved 100 out of 100.",
        title="Accounting Outcome",
        kind="course",
        aliases=(),
    )
    await memory.seed(
        "Weekend laundry and grocery shopping list: milk, eggs, bread.",
        title="Errands",
        kind="episode",
        aliases=(),
    )
    runtime = MemoryRuntime(memory.store)
    packet = await runtime.prepare(
        memory.context("How did I perform on my university tests?", agent_id="speda")
    )
    topics = [json.loads(b).get("topic", "") for b in packet.blocks if "current_state" not in json.loads(b)]
    assert "Statistics I Result" in topics
    assert "Accounting Outcome" in topics
    assert "Errands" not in topics[:2]


@pytest.mark.asyncio
async def test_automatic_sync_on_publish_update_revoke_and_model_version_change(memory):
    """Embeddings auto-sync on publish, purge on revoke, and rebuild on model_id change."""
    source_id, _ = await memory.seed(
        "Owner attends OSTIM Technical University studying Management Information Systems.",
        title="University Record",
        kind="course",
        aliases=(),
    )
    async with memory.sessions() as db:
        rows = (await db.execute(select(MemoryEmbedding).where(
            MemoryEmbedding.user_id == 1, MemoryEmbedding.target_kind == "unit"
        ))).scalars().all()
        assert len(rows) == 1
        assert rows[0].model_id == memory.store.semantic.model_id
        assert rows[0].dims == 384

    # Simulate a model-version upgrade: stale model_id rows must be purged and re-embedded
    async with memory.sessions() as db, db.begin():
        rows = (await db.execute(select(MemoryEmbedding).where(MemoryEmbedding.user_id == 1))).scalars().all()
        for r in rows:
            r.model_id = "old/legacy-model-v0"
    memory.store.semantic._buckets.clear()
    async with memory.sessions() as db, db.begin():
        rebuilt = await memory.store.semantic.sync_units_for_user(db, 1)
        assert rebuilt == 1
    async with memory.sessions() as db:
        rows = (await db.execute(select(MemoryEmbedding).where(MemoryEmbedding.user_id == 1))).scalars().all()
        assert len(rows) == 1
        assert rows[0].model_id == memory.store.semantic.model_id

    # Revoking a source removes its source embedding and prevents recall
    await memory.store.revoke(memory.context(), source_id)
    async with memory.sessions() as db:
        src_embs = (await db.execute(select(MemoryEmbedding).where(
            MemoryEmbedding.user_id == 1,
            MemoryEmbedding.target_kind == "source",
            MemoryEmbedding.target_id == source_id,
        ))).scalars().all()
        assert src_embs == []


@pytest.mark.asyncio
async def test_user_isolation_and_rollback_flag(memory):
    """User 2 embeddings never leak to User 1; disabling semantic falls back cleanly to BM25."""
    await memory.seed("Secret project Phoenix launch date is December 15.", owner=2, title="User2 Secret", kind="project", aliases=())
    await memory.seed("Owner likes strong Turkish coffee in the morning.", owner=1, title="Morning Coffee", kind="episode", aliases=())

    runtime = MemoryRuntime(memory.store)
    p1 = await runtime.prepare(memory.context("When is project Phoenix launching?", user_id=1))
    assert "Phoenix" not in packet_json(p1)

    # Rollback switch: semantic disabled -> BM25 still works cleanly
    memory.store.semantic.enabled = False
    p_bm25 = await runtime.prepare(memory.context("Turkish coffee", user_id=1))
    assert "Turkish coffee" in packet_json(p_bm25)


def test_rrf_and_mmr_promote_diverse_relevant_evidence():
    """RRF combines semantic + lexical ranks; MMR suppresses near-duplicate saturation."""
    fused = rrf_fuse([("a", 0.92), ("b", 0.88), ("c", 0.70)], [("b", 120.0), ("d", 100.0)])
    assert fused["b"] > fused["a"]  # item strong in both lanes wins RRF

    v_dup = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    v_alt = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    ordered = mmr_order([
        ("dup1", 1.00, v_dup),
        ("dup2", 0.99, v_dup),
        ("dup3", 0.98, v_dup),
        ("distinct", 0.92, v_alt),
    ], limit=2, lambda_mult=0.70)
    assert ordered == ["dup1", "distinct"]
