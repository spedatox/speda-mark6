"""Operation Iacon benchmark:
1. Sparse Bi-Temporal PPR scalability (1k / 5k / 10k nodes, actual RAM & p50/p95 ms).
2. 4-way baseline ablation (C0 BM25 vs. C1 Hybrid Semantic vs. C2 +Dreaming vs. C3 +Sparse PPR)
   on unseen multi-hop, cross-session, bilingual, and temporal queries.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from time import perf_counter
from types import SimpleNamespace

import numpy as np
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base
from app.models.user import User
from app.schemas.iacon import Command
from app.schemas.memory_runtime import MemoryBudget
from app.services.associative_graph import (
    INF_TS, NEG_INF_TS, EdgeRecord, SparseUserGraph, TemporalScope,
)
from app.services.iacon_catalogue import Catalogue
from app.services.iacon_dreaming import OrionDreamer
from app.services.memory_runtime import MemoryRuntime, packet_json
from app.services.memory_store import MemoryStore, capture_source, init_indexes


def benchmark_sparse_ppr_scalability() -> list[dict]:
    """Benchmark actual sparse O(V + E) PPR latency and array memory at 1k, 5k, 10k nodes."""
    rng = np.random.default_rng(42)
    results = []
    for n_nodes, avg_deg in ((1_000, 6), (5_000, 6), (10_000, 6)):
        n_edges = n_nodes * avg_deg
        src = rng.integers(0, n_nodes, size=n_edges, dtype=np.int32)
        dst = rng.integers(0, n_nodes, size=n_edges, dtype=np.int32)
        # Bidirectional arrays (2 * E)
        src2 = np.concatenate([src, dst])
        dst2 = np.concatenate([dst, src])
        w2 = rng.uniform(0.5, 1.0, size=src2.size).astype(np.float32)
        vf2 = np.full(src2.size, NEG_INF_TS, dtype=np.float64)
        vu2 = np.full(src2.size, INF_TS, dtype=np.float64)
        sup2 = np.full(src2.size, INF_TS, dtype=np.float64)
        meta2 = np.arange(src2.size, dtype=np.int32)

        node_ids = [f"n{i}" for i in range(n_nodes)]
        node_to_idx = {nid: i for i, nid in enumerate(node_ids)}
        graph = SparseUserGraph(
            user_id=1, epoch=1, node_ids=node_ids, node_to_idx=node_to_idx,
            node_titles={}, node_kinds={}, src_idx=src2, dst_idx=dst2,
            base_weight=w2, valid_from_ts=vf2, valid_until_ts=vu2,
            superseded_ts=sup2, edge_meta_idx=meta2, edges=[], adj=[], aliases={},
        )
        array_bytes = sum(
            arr.nbytes for arr in (src2, dst2, w2, vf2, vu2, sup2, meta2)
        )
        scope = TemporalScope.biographical(date(2026, 11, 23))
        # Warmup
        graph.personalized_pagerank({"n0": 1.0, "n1": 0.8}, scope=scope)
        latencies_ms = []
        for q in range(40):
            seeds = {f"n{q * 7 % n_nodes}": 1.0, f"n{(q * 13 + 3) % n_nodes}": 0.7}
            t0 = perf_counter()
            graph.personalized_pagerank(seeds, scope=scope, alpha=0.25, iterations=15, top_k=16)
            latencies_ms.append((perf_counter() - t0) * 1000.0)
        results.append({
            "nodes": n_nodes,
            "directed_edges": int(src2.size),
            "sparse_array_mb": round(array_bytes / (1024 * 1024), 2),
            "p50_ms": round(float(np.percentile(latencies_ms, 50)), 2),
            "p95_ms": round(float(np.percentile(latencies_ms, 95)), 2),
        })
    return results


async def _add(catalogue, sessions, key, title, kind, payload, quote):
    async with sessions() as db, db.begin():
        src = await capture_source(db, user_id=1, source_key=f"bench:{key}", content=quote, source_role="owner")
        sid = src.id
    return await catalogue.apply(1, Command(
        command_id=f"bench:{key}", kind=kind, title=title, payload=payload,
        evidence=[{"source_id": sid, "start": 0, "end": len(quote), "quote": quote}],
    ), actor="orion_review")


async def run_ablation_benchmark() -> dict:
    """Compare C0 (BM25) -> C1 (Hybrid) -> C2 (+Dreaming) -> C3 (+Sparse PPR) on unseen multi-hop queries."""
    with tempfile.TemporaryDirectory(prefix="speda-bench-") as tmp:
        db_path = Path(tmp) / "bench.db"
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await init_indexes(conn)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db, db.begin():
            db.add(User(id=1, name="Ahmet Erol Bayrak", timezone="Europe/Istanbul"))

        store = MemoryStore(sessions)
        catalogue = Catalogue(store)
        dreamer = OrionDreamer(catalogue)

        # Populate 4 distinct multi-session life arcs (1 canonical + 3 completely unseen multi-hop chains) + distractors
        # Arc 1 (Canonical): Hackathon -> Liva -> Yasemin -> Career Fair -> SIBERAY
        r_hack = await _add(catalogue, sessions, "hack", "Stellar Hackathon", "entity", {"entity_type": "project", "description": "Participated in Stellar hackathon Nov 2025."}, "In Nov 2025 I joined the Stellar Hackathon.")
        r_liva = await _add(catalogue, sessions, "liva", "Liva", "entity", {"entity_type": "person", "description": "Met at Stellar hackathon."}, "I met Liva at the hackathon.")
        r_yas = await _add(catalogue, sessions, "yas", "Yasemin", "entity", {"entity_type": "person", "description": "University friend."}, "I became friends with Yasemin at university.")
        r_fair = await _add(catalogue, sessions, "fair", "University Career Fair", "entity", {"entity_type": "place", "description": "Attended with Yasemin."}, "Went to the career fair with Yasemin.")
        r_sib = await _add(catalogue, sessions, "sib", "SIBERAY", "entity", {"entity_type": "organization", "description": "Cybersecurity club presidency."}, "Became president of SIBERAY.")
        await catalogue.upsert_edge(1, source_id=r_hack["id"], target_id=r_liva["id"], relation="met_at", valid_from="2025-11-10")
        await catalogue.upsert_edge(1, source_id=r_liva["id"], target_id=r_yas["id"], relation="subsequent_circle", epistemic_mode="inferred", valid_from="2025-12-01")
        await catalogue.upsert_edge(1, source_id=r_yas["id"], target_id=r_fair["id"], relation="attended_with", valid_from="2026-02-15")
        await catalogue.upsert_edge(1, source_id=r_fair["id"], target_id=r_sib["id"], relation="encountered_stand", valid_from="2026-02-15")

        # Arc 2 (Unseen): Robotics Workshop -> Prof Kerem -> Autonomous Drone Lab -> Teknofest Finals
        r_ws = await _add(catalogue, sessions, "ws", "Summer Robotics Workshop", "entity", {"entity_type": "project", "description": "Embedded control workshop."}, "Joined the Summer Robotics Workshop.")
        r_kerem = await _add(catalogue, sessions, "kerem", "Prof Kerem", "entity", {"entity_type": "person", "description": "Robotics faculty advisor."}, "Met Prof Kerem at the workshop.")
        r_lab = await _add(catalogue, sessions, "lab", "Autonomous Drone Lab", "entity", {"entity_type": "organization", "description": "UAV flight control lab."}, "Joined Autonomous Drone Lab.")
        r_tek = await _add(catalogue, sessions, "tek", "Teknofest Finals", "entity", {"entity_type": "project", "description": "Qualified for national finals."}, "Qualified for Teknofest Finals.")
        await catalogue.upsert_edge(1, source_id=r_ws["id"], target_id=r_kerem["id"], relation="met_at", valid_from="2025-07-12")
        await catalogue.upsert_edge(1, source_id=r_kerem["id"], target_id=r_lab["id"], relation="invited_to", valid_from="2025-09-05")
        await catalogue.upsert_edge(1, source_id=r_lab["id"], target_id=r_tek["id"], relation="qualified_for", valid_from="2026-04-20")

        # Arc 3 (Unseen): Izmir Coffee Meetup -> Deniz -> Open Source Rust Kernel -> Cloud Internship
        r_izm = await _add(catalogue, sessions, "izm", "Izmir Systems Meetup", "entity", {"entity_type": "place", "description": "Weekend systems engineering meetup."}, "Attended the Izmir Systems Meetup.")
        r_den = await _add(catalogue, sessions, "den", "Deniz", "entity", {"entity_type": "person", "description": "Rust systems engineer."}, "Met Deniz at the Izmir meetup.")
        r_rust = await _add(catalogue, sessions, "rust", "Nebula Rust Kernel", "entity", {"entity_type": "project", "description": "Open-source microkernel repo."}, "Contributed patches to Nebula Rust Kernel with Deniz.")
        r_int = await _add(catalogue, sessions, "int", "CloudCore Internship", "entity", {"entity_type": "organization", "description": "Summer infrastructure internship."}, "Landed CloudCore Internship after my Nebula Rust Kernel PRs.")
        await catalogue.upsert_edge(1, source_id=r_izm["id"], target_id=r_den["id"], relation="met_at", valid_from="2025-08-03")
        await catalogue.upsert_edge(1, source_id=r_den["id"], target_id=r_rust["id"], relation="collaborated_on", valid_from="2025-10-10")
        await catalogue.upsert_edge(1, source_id=r_rust["id"], target_id=r_int["id"], relation="led_to_interview", valid_from="2026-05-01")

        # Arc 4 (Unseen Bilingual): Ankara Book Club -> Selin -> Machine Learning Study Group -> Statistics Paper
        r_bk = await _add(catalogue, sessions, "bk", "Ankara Kitap Kulubu", "entity", {"entity_type": "organization", "description": "Haftalik okuma grubu."}, "Ankara Kitap Kulubu etkinliklerine katildim.")
        r_sel = await _add(catalogue, sessions, "sel", "Selin", "entity", {"entity_type": "person", "description": "Istatistik yuksek lisans ogrencisi."}, "Kulubde Selin ile tanistim.")
        r_ml = await _add(catalogue, sessions, "ml", "Bayesian Study Group", "entity", {"entity_type": "organization", "description": "Weekly probability reading seminar."}, "Selin invited me to the Bayesian Study Group.")
        r_pap = await _add(catalogue, sessions, "pap", "Causal Inference Preprint", "entity", {"entity_type": "project", "description": "Co-authored statistics preprint."}, "We co-authored the Causal Inference Preprint.")
        await catalogue.upsert_edge(1, source_id=r_bk["id"], target_id=r_sel["id"], relation="met_at", valid_from="2025-06-15")
        await catalogue.upsert_edge(1, source_id=r_sel["id"], target_id=r_ml["id"], relation="invited_to", valid_from="2025-10-01")
        await catalogue.upsert_edge(1, source_id=r_ml["id"], target_id=r_pap["id"], relation="coauthored", valid_from="2026-03-10")

        # Add 12 unrelated distractor records
        for i in range(12):
            await _add(catalogue, sessions, f"dist{i}", f"Daily Errand Note {i}", "entity",
                       {"entity_type": "place", "description": f"Routine grocery or errand stop number {i}."},
                       f"Picked up routine household supplies at stop {i}.")

        await dreamer.dream_user(1, use_llm=False)

        eval_queries = [
            ("Canonical 4-hop", "How did that hackathon change my university life?", ["Stellar Hackathon", "Liva", "Yasemin", "University Career Fair", "SIBERAY"]),
            ("Unseen 3-hop (Robotics)", "How did I end up qualifying for Teknofest Finals?", ["Summer Robotics Workshop", "Prof Kerem", "Autonomous Drone Lab", "Teknofest Finals"]),
            ("Unseen 3-hop (Systems)", "What chain of events led to my CloudCore Internship?", ["Izmir Systems Meetup", "Deniz", "Nebula Rust Kernel", "CloudCore Internship"]),
            ("Unseen Bilingual 3-hop", "How did my reading club in Ankara connect to my statistics preprint?", ["Ankara Kitap Kulubu", "Selin", "Bayesian Study Group", "Causal Inference Preprint"]),
        ]

        configs = [
            ("C0_BM25", False, False, False),
            ("C1_Hybrid_Semantic", True, False, False),
            ("C2_Plus_Dreaming", True, True, False),
            ("C3_Full_Operation_Iacon", True, True, True),
        ]

        report = {}
        for name, sem_on, dream_on, ppr_on in configs:
            store.semantic.enabled = sem_on
            store.graph.enabled = ppr_on
            os.environ["SPEDA_DREAMING_CONTEXT"] = "true" if dream_on else "false"
            runtime = MemoryRuntime(store)

            recalls = []
            latencies = []
            per_query = []
            for label, q_text, required in eval_queries:
                ctx = SimpleNamespace(
                    request_id=f"bench-{name}", user_id=1, agent_id="speda",
                    timezone="Europe/Istanbul", triggered_by="user", trigger_payload={},
                    parent_step_id=None, model_call_index=0, system_prompt=None,
                    task_role="interactive", run_id=None, room_id=None, project_id=None,
                    session_id=99, memory_source_id=None, allowed_tools=("memory_lookup",),
                    conversation_history=[{"role": "user", "content": q_text}],
                    memory_budget=MemoryBudget(), extra={},
                )
                t0 = perf_counter()
                pkt = await runtime.prepare(ctx)
                dt_ms = (perf_counter() - t0) * 1000.0
                blob = packet_json(pkt)
                found = [tok for tok in required if tok in blob]
                rec = len(found) / len(required)
                recalls.append(rec)
                latencies.append(dt_ms)
                per_query.append({"query": label, "recall": round(rec, 3), "found": len(found), "total": len(required), "ms": round(dt_ms, 1)})
            report[name] = {
                "avg_multihop_recall": round(float(np.mean(recalls)), 3),
                "full_chain_hits": f"{sum(1 for r in recalls if r == 1.0)}/{len(recalls)}",
                "avg_ms": round(float(np.mean(latencies)), 1),
                "p95_ms": round(float(np.percentile(latencies, 95)), 1),
                "details": per_query,
            }
        await engine.dispose()
        return report


async def main():
    ppr_scale = benchmark_sparse_ppr_scalability()
    ablation = await run_ablation_benchmark()
    print(json.dumps({"sparse_ppr_scalability": ppr_scale, "ablation_matrix": ablation}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
