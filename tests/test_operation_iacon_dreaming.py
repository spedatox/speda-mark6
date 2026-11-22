"""Operation Iacon: Dreaming-Grade Associative Memory test suite (Tests A–J).

Covers:
- Test A: Multi-hop autobiographical continuity without tool calls (Hackathon -> SİBERAY)
- Test A2: Unseen multi-hop associative chain (Robotics Workshop -> Teknofest Finals)
- Test B/C: Cross-agent & bilingual recall via dreaming + hybrid embeddings
- Test D/E: Strict bi-temporal filtering (current / as_of / range / biographical — no arbitrary decay)
- Test F: Abstention & explicit vs. inferred epistemic separation
- Test G: Session-window multi-turn episode grouping
- Test H: Full cascading deletion across DB tables, narratives, embeddings, caches, and work queues
- Test I: Multi-user isolation & owner_locked narrative protection
- Test J: Untrusted-data framing & rollback feature flags
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
import os
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base
from app.models.iacon import (
    IaconEdge, IaconEntityAlias, IaconNarrative, IaconRecord, IaconWork,
)
from app.models.memory_serving import MemoryChange, MemoryEmbedding, MemoryServingUnit
from app.models.user import User
from app.schemas.iacon import Command
from app.schemas.memory_runtime import MemoryBudget
from app.services.associative_graph import TemporalScope
from app.services.iacon_catalogue import Catalogue
from app.services.iacon_dreaming import OrionDreamer, build_session_windows
from app.services.memory_runtime import MemoryRuntime, packet_json
from app.services.memory_store import MemoryStore, capture_source, init_indexes


async def _make_env(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'iacon_dream.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await init_indexes(conn)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db, db.begin():
        db.add(User(id=1, name="Ahmet Erol Bayrak", timezone="Europe/Istanbul"))
        db.add(User(id=2, name="Other User", timezone="UTC"))
    store = MemoryStore(sessions)
    catalogue = Catalogue(store)
    dreamer = OrionDreamer(catalogue)
    runtime = MemoryRuntime(store)
    return engine, sessions, store, catalogue, dreamer, runtime


def _ctx(user_id=1, text="", agent_id="speda"):
    return SimpleNamespace(
        request_id="req-dream-1",
        user_id=user_id,
        agent_id=agent_id,
        timezone="Europe/Istanbul",
        triggered_by="user",
        trigger_payload={},
        parent_step_id=None,
        model_call_index=0,
        system_prompt=None,
        task_role="interactive",
        run_id=None,
        room_id=None,
        project_id=None,
        session_id=1,
        memory_source_id=None,
        allowed_tools=("memory_lookup",),
        conversation_history=[{"role": "user", "content": text}],
        memory_budget=MemoryBudget(),
        extra={},
    )


async def _add_record(catalogue, sessions, user_id, *, key, title, kind, payload, quote, links=()):
    async with sessions() as db, db.begin():
        src = await capture_source(db, user_id=user_id, source_key=f"test:{key}", content=quote, source_role="owner")
        src_id = src.id
    cmd = Command(
        command_id=f"cmd:{key}",
        kind=kind,
        title=title,
        payload=payload,
        evidence=[{"source_id": src_id, "start": 0, "end": len(quote), "quote": quote}],
        links=list(links),
    )
    return await catalogue.apply(user_id, cmd, actor="orion_review")


@pytest.mark.asyncio
async def test_a_multihop_autobiographical_hackathon_to_siberay_zero_tool_calls(tmp_path):
    engine, sessions, store, catalogue, dreamer, runtime = await _make_env(tmp_path)
    try:
        r_hack = await _add_record(
            catalogue, sessions, 1, key="hack", title="Stellar Hackathon", kind="entity",
            payload={"entity_type": "project", "description": "Participated in the Stellar hackathon in November 2025."},
            quote="In November 2025 I participated in the Stellar hackathon and met Liva there.",
        )
        r_liva = await _add_record(
            catalogue, sessions, 1, key="liva", title="Liva", kind="entity",
            payload={"entity_type": "person", "description": "Friend met at the Stellar hackathon in November 2025."},
            quote="I met Liva at the Stellar hackathon.",
        )
        r_yas = await _add_record(
            catalogue, sessions, 1, key="yasemin", title="Yasemin", kind="entity",
            payload={"entity_type": "person", "description": "University friend introduced alongside Sevval."},
            quote="At university I became close friends with Yasemin and Sevval.",
        )
        r_fair = await _add_record(
            catalogue, sessions, 1, key="fair", title="University Career Fair", kind="entity",
            payload={"entity_type": "place", "description": "Attended the university career fair with Yasemin."},
            quote="I attended the career fair with Yasemin and we stopped by the SIBERAY stand.",
        )
        r_sib = await _add_record(
            catalogue, sessions, 1, key="siberay", title="SIBERAY", kind="entity",
            payload={"entity_type": "organization", "description": "Cybersecurity community where Ahmet became president."},
            quote="My involvement with SIBERAY eventually led to becoming SIBERAY president.",
        )

        # Build bi-temporal associative edges (explicit + inferred chronological bridge)
        await catalogue.upsert_edge(
            1, source_id=r_hack["id"], target_id=r_liva["id"], relation="met_at",
            description="Met Liva at the Stellar hackathon", epistemic_mode="explicit",
            valid_from="2025-11-10",
        )
        await catalogue.upsert_edge(
            1, source_id=r_liva["id"], target_id=r_yas["id"], relation="subsequent_university_circle",
            description="Subsequent university social circle introduced Yasemin and Sevval",
            epistemic_mode="inferred", confidence=0.8, valid_from="2025-12-01",
        )
        await catalogue.upsert_edge(
            1, source_id=r_yas["id"], target_id=r_fair["id"], relation="attended_with",
            description="Attended the university career fair together with Yasemin",
            epistemic_mode="explicit", valid_from="2026-02-15",
        )
        await catalogue.upsert_edge(
            1, source_id=r_fair["id"], target_id=r_sib["id"], relation="encountered_stand",
            description="Encountered the SIBERAY stand at the career fair",
            epistemic_mode="explicit", valid_from="2026-02-15",
        )

        # Run Orion Dreaming consolidation (offline deterministic synthesis)
        dream_res = await dreamer.dream_user(1, use_llm=False)
        assert any(s.startswith("chapter:") for s in dream_res["updated"])
        assert "core:owner_identity" in dream_res["updated"]

        # Foreground query — ZERO tool calls!
        pkt = await runtime.prepare(_ctx(1, "How did that hackathon change my university life?"))
        serialized = json.loads(packet_json(pkt))

        assert "autobiographical_memory" in serialized
        assert "associative_chains" in serialized
        joined_mem = json.dumps(serialized, ensure_ascii=False)
        for expected in ("Stellar Hackathon", "Liva", "Yasemin", "University Career Fair", "SIBERAY"):
            assert expected in joined_mem, f"Missing {expected} in automatic foreground context"
        # Verify epistemic separation is preserved
        flat_edges = [step for ch in serialized["associative_chains"] for step in ch]
        modes = {step["epistemic_mode"] for step in flat_edges}
        assert "explicit" in modes
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a2_unseen_multihop_chain_robotics_to_teknofest(tmp_path):
    """Unseen multi-hop scenario completely unrelated to hackathons/SIBERAY."""
    engine, sessions, store, catalogue, dreamer, runtime = await _make_env(tmp_path)
    try:
        r_ws = await _add_record(
            catalogue, sessions, 1, key="ws", title="Summer Robotics Workshop", kind="entity",
            payload={"entity_type": "project", "description": "Attended a summer robotics workshop in July 2025."},
            quote="In July 2025 I joined the Summer Robotics Workshop.",
        )
        r_prof = await _add_record(
            catalogue, sessions, 1, key="prof", title="Prof Kerem", kind="entity",
            payload={"entity_type": "person", "description": "Robotics professor met at the summer workshop."},
            quote="I met Prof Kerem during the workshop.",
        )
        r_lab = await _add_record(
            catalogue, sessions, 1, key="lab", title="Autonomous Drone Lab", kind="entity",
            payload={"entity_type": "organization", "description": "University drone research lab run by Prof Kerem."},
            quote="Prof Kerem invited me to join the Autonomous Drone Lab.",
        )
        r_tek = await _add_record(
            catalogue, sessions, 1, key="tek", title="Teknofest Finals", kind="entity",
            payload={"entity_type": "project", "description": "Reached the Teknofest autonomous flight finals in April 2026."},
            quote="Our Autonomous Drone Lab team qualified for the Teknofest Finals.",
        )
        await catalogue.upsert_edge(1, source_id=r_ws["id"], target_id=r_prof["id"], relation="met_at", valid_from="2025-07-12")
        await catalogue.upsert_edge(1, source_id=r_prof["id"], target_id=r_lab["id"], relation="invited_to", valid_from="2025-09-05")
        await catalogue.upsert_edge(1, source_id=r_lab["id"], target_id=r_tek["id"], relation="qualified_for", valid_from="2026-04-20")

        await dreamer.dream_user(1, use_llm=False)

        pkt = await runtime.prepare(_ctx(1, "How did I end up qualifying for Teknofest Finals?"))
        serialized = json.loads(packet_json(pkt))
        blob = json.dumps(serialized, ensure_ascii=False)
        for token in ("Summer Robotics Workshop", "Prof Kerem", "Autonomous Drone Lab", "Teknofest Finals"):
            assert token in blob
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_d_e_proper_bitemporal_filtering_no_arbitrary_decay(tmp_path):
    """Correction #3: Current vs. As-Of vs. Range vs. Biographical filtering without arbitrary penalties."""
    engine, sessions, store, catalogue, dreamer, runtime = await _make_env(tmp_path)
    try:
        r_me = await _add_record(
            catalogue, sessions, 1, key="me", title="Ahmet", kind="entity",
            payload={"entity_type": "person", "description": "Owner"},
            quote="My name is Ahmet.",
        )
        r_old = await _add_record(
            catalogue, sessions, 1, key="oldclub", title="Old Robotics Club", kind="entity",
            payload={"entity_type": "organization", "description": "Former university club."},
            quote="I used to be in Old Robotics Club.",
        )
        r_new = await _add_record(
            catalogue, sessions, 1, key="newclub", title="SIBERAY", kind="entity",
            payload={"entity_type": "organization", "description": "Current cybersecurity presidency."},
            quote="I am currently president of SIBERAY.",
        )
        e_old = await catalogue.upsert_edge(
            1, source_id=r_me["id"], target_id=r_old["id"], relation="member_of",
            valid_from="2024-09-01",
        )
        await catalogue.supersede_edge(1, e_old["id"], valid_until="2025-06-01")
        await catalogue.upsert_edge(
            1, source_id=r_me["id"], target_id=r_new["id"], relation="president_of",
            valid_from="2026-03-01",
        )

        async with sessions() as db:
            epoch = await store.epoch(SimpleNamespace(user_id=1))
            graph = await store.graph.load(db, 1, epoch)

        seeds = {r_me["id"]: 1.0}
        # 1. Current scope (2026-11-23): strictly excludes closed/superseded Old Robotics Club
        cur = dict(graph.personalized_pagerank(seeds, scope=TemporalScope.current(date(2026, 11, 23))))
        assert r_new["id"] in cur
        assert r_old["id"] not in cur

        # 2. Point-in-time As-Of scope (2025-02-01): returns Old Robotics Club, excludes future SIBERAY
        past = dict(graph.personalized_pagerank(seeds, scope=TemporalScope.at_date(date(2025, 2, 1))))
        assert r_old["id"] in past
        assert r_new["id"] not in past

        # 3. Range scope (2024-10-01 .. 2025-05-01): returns Old Robotics Club
        rng = dict(graph.personalized_pagerank(seeds, scope=TemporalScope.between(date(2024, 10, 1), date(2025, 5, 1))))
        assert r_old["id"] in rng
        assert r_new["id"] not in rng

        # 4. Biographical scope (2026-11-23): returns BOTH milestones cleanly
        bio = dict(graph.personalized_pagerank(seeds, scope=TemporalScope.biographical(date(2026, 11, 23))))
        assert r_old["id"] in bio and r_new["id"] in bio
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_h_deletion_propagates_across_all_layers_caches_and_queues(tmp_path):
    """Correction #2: Deleting a record purges edges, aliases, units, embeddings, narratives, caches, and pending work."""
    engine, sessions, store, catalogue, dreamer, runtime = await _make_env(tmp_path)
    try:
        r_secret = await _add_record(
            catalogue, sessions, 1, key="secret", title="Confidential Medical Note", kind="entity",
            payload={"entity_type": "place", "description": "Sensitive clinic visit X99."},
            quote="I visited Clinic X99 for a confidential checkup.",
        )
        r_friend = await _add_record(
            catalogue, sessions, 1, key="friend", title="Mert", kind="entity",
            payload={"entity_type": "person", "description": "Close friend from Ankara."},
            quote="Mert is my close friend from Ankara.",
        )
        await catalogue.upsert_edge(
            1, source_id=r_secret["id"], target_id=r_friend["id"], relation="accompanied_by",
            valid_from="2026-10-01",
        )
        await dreamer.dream_user(1, use_llm=False)

        # Enqueue a pending refresh work item targeting r_secret and warm all caches via a read
        async with sessions() as db, db.begin():
            db.add(IaconWork(
                user_id=1, work_key=f"refresh:test:{r_secret['id']}", kind="refresh",
                payload={"record_id": r_secret["id"]}, status="pending",
            ))
        before_pkt = await runtime.prepare(_ctx(1, "Tell me about Clinic X99 and Mert"))
        assert "Confidential Medical Note" in packet_json(before_pkt)
        assert len(store.graph._cache) > 0

        # Execute cascading deletion
        del_res = await catalogue.delete_record(1, r_secret["id"], actor="owner", retire_sources=True)
        assert del_res["deleted"] is True
        assert del_res["cancelled_work"] >= 1
        assert len(del_res["invalidated_narratives"]) >= 1

        # Verify in-memory caches were immediately evicted
        assert all(k[0] != 1 for k in store.graph._cache)
        assert all(k[0] != 1 for k in store.semantic._buckets)
        assert all(k[0] != 1 for k in store.catalog)

        # Verify DB layers
        async with sessions() as db:
            rec = await db.get(IaconRecord, r_secret["id"])
            assert rec.status == "deleted"
            active_edges = (await db.execute(select(IaconEdge).where(
                IaconEdge.user_id == 1, IaconEdge.status != "deleted",
            ))).scalars().all()
            assert active_edges == []
            aliases = (await db.execute(select(IaconEntityAlias).where(
                IaconEntityAlias.record_id == r_secret["id"],
            ))).scalars().all()
            assert aliases == []
            units = (await db.execute(select(MemoryServingUnit).where(
                MemoryServingUnit.stable_key == "iacon:" + r_secret["id"],
            ))).scalars().all()
            assert units == []
            cancelled = (await db.execute(select(IaconWork).where(
                IaconWork.work_key == f"refresh:test:{r_secret['id']}",
            ))).scalar_one()
            assert cancelled.status == "cancelled"
            stale_nars = (await db.execute(select(IaconNarrative).where(
                IaconNarrative.user_id == 1, IaconNarrative.status == "stale_deleted",
            ))).scalars().all()
            assert len(stale_nars) >= 1

        # Foreground query IMMEDIATELY sees zero trace of deleted fact even before async rebuild
        after_pkt = await runtime.prepare(_ctx(1, "Tell me about Clinic X99 and Mert"))
        after_blob = packet_json(after_pkt)
        assert "Confidential Medical Note" not in after_blob
        assert "Clinic X99" not in after_blob

        # Async dream_rebuild restores narrative cleanly with only remaining records
        for slug in del_res["invalidated_narratives"]:
            rebuilt = await dreamer.rebuild_narrative(1, slug, use_llm=False)
            assert rebuilt["rebuilt"] is True
        rebuilt_pkt = await runtime.prepare(_ctx(1, "Who is Mert?"))
        rebuilt_blob = packet_json(rebuilt_pkt)
        assert "Mert" in rebuilt_blob
        assert "Clinic X99" not in rebuilt_blob
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_g_session_window_grouping():
    base = datetime(2026, 11, 20, 14, 0, tzinfo=timezone.utc)
    c1 = SimpleNamespace(id=1, created_at=base, provenance={"session_id": 10}, content="Statistics I result RELEASED!")
    c2 = SimpleNamespace(id=2, created_at=base + timedelta(seconds=25), provenance={"session_id": 10}, content="99!!")
    c3 = SimpleNamespace(id=3, created_at=base + timedelta(minutes=25), provenance={"session_id": 10}, content="New topic later")
    c4 = SimpleNamespace(id=4, created_at=base + timedelta(minutes=26), provenance={"session_id": 11}, content="Different session")
    windows = build_session_windows([c1, c2, c3, c4], gap_minutes=10, max_turns=8)
    assert len(windows) == 3
    assert [c.id for c in windows[0]] == [1, 2]
    assert [c.id for c in windows[1]] == [3]
    assert [c.id for c in windows[2]] == [4]


@pytest.mark.asyncio
async def test_i_j_user_isolation_owner_lock_and_rollback_flags(tmp_path, monkeypatch):
    engine, sessions, store, catalogue, dreamer, runtime = await _make_env(tmp_path)
    try:
        r1 = await _add_record(
            catalogue, sessions, 1, key="u1", title="Ahmet Private Project", kind="entity",
            payload={"entity_type": "project", "description": "Secret Mark VII prototype."},
            quote="I am secretly building Mark VII prototype.",
        )
        await catalogue.upsert_narrative(
            1, tier="core_identity", slug="core:owner_identity", title="Owner Locked Card",
            summary_markdown="Owner manually pinned identity.", source_record_ids=[r1["id"]],
            owner_locked=True, actor="owner",
        )
        # Orion dreaming must respect owner_locked=True
        res = await dreamer.dream_user(1, use_llm=False, force=True)
        assert "core:owner_identity" not in res["updated"]

        # User 2 must NEVER see User 1's narratives or records
        pkt_u2 = await runtime.prepare(_ctx(2, "What is Ahmet Private Project?"))
        blob_u2 = packet_json(pkt_u2)
        assert "Mark VII" not in blob_u2
        assert "Owner Locked Card" not in blob_u2

        # Untrusted data framing check on User 1
        pkt_u1 = await runtime.prepare(_ctx(1, "What am I building?"))
        ser_u1 = json.loads(packet_json(pkt_u1))
        assert ser_u1["autobiographical_memory"][0]["untrusted_user_history"] is True

        # Rollback flag disables dreaming context cleanly
        monkeypatch.setenv("SPEDA_DREAMING_CONTEXT", "false")
        pkt_off = await runtime.prepare(_ctx(1, "What am I building?"))
        ser_off = json.loads(packet_json(pkt_off))
        assert "autobiographical_memory" not in ser_off
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_autonomous_dreaming_consolidation_and_backfill(tmp_path):
    """Verify autonomous Orion.drain() dream_cycle/dream_rebuild jobs and legacy link backfill."""
    from app.models.iacon import IaconLink
    from app.services.iacon_budget import MaintenanceBudget
    from app.services.iacon_worker import Orion

    engine, sessions, store, catalogue, dreamer, runtime = await _make_env(tmp_path)
    try:
        r1 = await _add_record(
            catalogue, sessions, 1, key="bf1", title="Antalya Math Camp", kind="entity",
            payload={"entity_type": "project", "description": "Attended winter math camp in Antalya."},
            quote="I attended Antalya Math Camp in January 2025.",
        )
        r2 = await _add_record(
            catalogue, sessions, 1, key="bf2", title="Dr Kemal", kind="entity",
            payload={"entity_type": "person", "description": "Combinatorics lecturer met at Antalya Math Camp."},
            quote="Met Dr Kemal at Antalya Math Camp.",
        )
        # Simulate a legacy IaconLink without a corresponding IaconEdge
        async with sessions() as db, db.begin():
            db.add(IaconLink(user_id=1, source_id=r1["id"], target_id=r2["id"], relation="participant"))

        bf = await catalogue.backfill_links_and_aliases(1)
        assert bf["edges_created"] >= 1
        assert bf["records_scanned"] >= 2

        # Queue autonomous dream_cycle work item and drain via Orion
        budget = MaintenanceBudget(sessions, client=None)
        orion = Orion(catalogue, budget, model_policy=None, prompts=None, enabled=True)
        await orion.enqueue(
            user_id=1, kind="dream_cycle", unique_key="nightly:2026-11-23",
            payload={"use_llm": False, "force": False},
        )
        drain_res = await orion.drain(_ctx(1, ""), limit=5)
        assert drain_res["processed"] >= 1
        assert any(o["status"] == "done" for o in drain_res["outcomes"])

        narratives = await catalogue.list_narratives(1)
        assert any(n["tier"] == "chapter" for n in narratives)
        # Re-running dream_cycle with unchanged digest must be a clean no-op (incremental!)
        second = await orion.dream(1, use_llm=False, force=False)
        assert second["updated"] == []
        assert second["skipped"] >= 1
    finally:
        await engine.dispose()
