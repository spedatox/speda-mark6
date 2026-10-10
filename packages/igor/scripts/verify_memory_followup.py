"""Non-destructive follow-up checks on an offline reconstruction candidate.

Production access is deliberately outside this script. Native search functions
make genuine provider requests; no stored or prepared query vectors are used.
"""
import argparse
import asyncio
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import time
from types import SimpleNamespace

from app.services.memory_cleanup import CorpusCleanup, file_hash
from reconstruct_memory_snapshot import validate_database, smoke


def readonly(path):
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def graph_repair(original, base, output, quarantine):
    if output.exists():
        raise ValueError("Fresh staging output required")
    with closing(readonly(base)) as source, closing(sqlite3.connect(output)) as target:
        source.backup(target)
    decisions = []
    with closing(readonly(original)) as source, closing(sqlite3.connect(output)) as db:
        db.row_factory = sqlite3.Row
        with db:
            for item in quarantine:
                edge = dict(source.execute("SELECT * FROM memory_graph_edges WHERE id=?", (item["id"],)).fetchone())
                match = re.fullmatch(r"path:observation:(\d+)", edge["target_ref"])
                if edge["relation_type"] == "changed" and match:
                    revision = db.execute("SELECT * FROM memory_revisions WHERE id=?", (int(edge["source_ref"].split(":")[1]),)).fetchone()
                    fact = db.execute("SELECT id FROM observations WHERE id=?", (int(match[1]),)).fetchone()
                    assert revision and fact and revision["path"] == edge["target_ref"][5:]
                    corrected = {**edge, "target_ref": "observation:" + match[1]}
                    db.execute("INSERT INTO memory_graph_edges (" + ",".join(corrected) + ") VALUES (" + ",".join("?" for _ in corrected) + ")", tuple(corrected.values()))
                    decisions.append({"edge_id": edge["id"], "disposition": "restored_with_native_ref", "original": edge, "corrected": corrected, "proof": "Exact revision.path plus existing observation ID; relationship and timestamp unchanged"})
                else:
                    assert edge["relation_type"] == "evidenced_by"
                    mid = int(edge["target_ref"].split(":")[1])
                    assert source.execute("SELECT id FROM messages WHERE id=?", (mid,)).fetchone() is None
                    assert db.execute("SELECT id FROM messages WHERE id=?", (mid,)).fetchone() is None
                    decisions.append({"edge_id": edge["id"], "disposition": "retained_quarantine_missing_evidence", "original": edge, "missing_message": mid, "knowledge_limit": "Claim/revision retained; missing owner utterance cannot be recovered or authenticated from supplied snapshot"})
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
    unchanged = []
    with closing(readonly(base)) as source, closing(readonly(output)) as dest:
        for row in source.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            table = row[0]
            if table == "memory_graph_edges":
                continue
            assert CorpusCleanup.table_hash(source, table) == CorpusCleanup.table_hash(dest, table), table
            unchanged.append(table)
    return {"decisions": decisions, "counts": dict(Counter(d["disposition"] for d in decisions)), "all_other_tables_logically_identical": unchanged}


async def provider_checks(output, artifacts, extra_cases=None, configuration_scope=None):
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.config import settings
    from app.services import embeddings
    from app.services.observations import search_observations
    from app.skills.semantic_search import SemanticSearchSkill
    from app.services.memory_graph import build_context

    requests = []
    async def received(response):
        if response.request.url.path.endswith("/embeddings"):
            requests.append({"status": response.status_code, "request_id": response.headers.get("x-request-id"), "provider_host": response.request.url.host})
    client = embeddings._get_client()
    client._client.event_hooks["response"].append(received)
    engine = create_async_engine("sqlite+aiosqlite:///" + output.as_posix())
    @event.listens_for(engine.sync_engine, "connect")
    def read_guard(conn, _):
        conn.execute("PRAGMA query_only=ON")
    cases = [
        ("personal", "Why does Ahmet prefer spending vacations with his family in Bursa instead of staying alone in Ankara?", {2108, 2110}),
        ("academic", "Which web application programming class does Ahmet attend on Tuesdays?", {2340, 2412, 2413}),
        ("project", "What is the Strategic Leverage Protocol's relationship to the Blackwalnut project?", {313, 314, 316, 317}),
        ("relationship", "Has Ahmet withdrawn the rule about Sinan Kara's nickname?", {2440}),
        ("temporal", "Does Ahmet still perform information technology work at OSTIM, or only attend a Tuesday class?", {2412, 2413}),
    ]
    cases.extend(extra_cases or [])
    results = []
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            for category, query, expected in cases:
                diagnostics = {}
                start = time.monotonic()
                before = len(requests)
                hits = await search_observations(db, user_id=1, query=query, as_of="2026-10-10", limit=10, diagnostics=diagnostics)
                ids = [o.id for o, _ in hits]
                trace = diagnostics.get("recall_trace", [{}])[-1]
                vector_matches = [x["id"] for x in trace.get("ranked_candidates", []) if x["vector_hit"] and x["id"] in expected]
                results.append({"category": category, "query": query, "expected_ids": sorted(expected), "returned_ids": ids, "expected_vector_hits": vector_matches, "provider_requests": requests[before:], "diagnostics": diagnostics, "elapsed_seconds": round(time.monotonic()-start, 3), "pass": bool(expected.intersection(ids)) and bool(vector_matches) and any(r["status"] == 200 for r in requests[before:]) and not diagnostics.get("recall_errors")})
            context = SimpleNamespace(db=db, user_id=1, agent_id="speda", session_id=1, request_id="offline-provider-validation", timezone="Europe/Istanbul", extra={})
            before = len(requests)
            conversation = await SemanticSearchSkill().execute({"query": "When did Ahmet say his IT work at OSTIM was finished and that he would only come for Tuesday classes?", "limit": 10, "context_window": 1}, context)
            (artifacts/"conversation-provider-output.txt").write_text(conversation, encoding="utf-8")
            trace = context.extra.get("recall_trace", [{}])[-1]
            vector_ids = [x["message_id"] for x in trace.get("ranked_candidates", []) if x["vector_hit"]]
            history = {"expected_message": 24717, "returned_expected_message": "message:24717" in conversation, "expected_in_vector_ranking": 24717 in vector_ids, "trace": context.extra, "provider_requests": requests[before:], "pass": "message:24717" in conversation and 24717 in vector_ids and any(r["status"] == 200 for r in requests[before:]) and not context.extra.get("recall_errors")}
            graph = await build_context(db, 1, "revision:1029", depth=1, max_edges=24)
            (artifacts/"restored-graph-output.txt").write_text(graph, encoding="utf-8")
            assert "observation:105" in graph
        return {"tested_at_utc": datetime.now(timezone.utc).isoformat(), "provider": "OpenAI", "embedding_model": settings.embedding_model, "configuration_scope": configuration_scope or "Local configured provider; live deployment settings inaccessible", "fact_similarity_floor": settings.recall_min_similarity, "conversation_similarity_floor": settings.recall_message_min_similarity, "fact_tests": results, "conversation_test": history, "provider_requests": requests, "restored_graph_native_retrieval_pass": True, "all_semantic_tests_pass": all(r["pass"] for r in results) and history["pass"]}
    finally:
        await engine.dispose()
        await client.close()


def carry_accounting(previous_artifacts, artifacts, graph_audit, output):
    """Carry the exhaustive source ledger forward, correcting restored edges."""
    restored = {d["edge_id"]: d for d in graph_audit["decisions"] if d["disposition"] == "restored_with_native_ref"}
    counts = Counter()
    updated = 0
    with closing(readonly(output)) as db, (previous_artifacts/"record-dispositions.jsonl").open(encoding="utf-8") as source, (artifacts/"record-dispositions.jsonl").open("w", encoding="utf-8") as target:
        for line in source:
            row = json.loads(line)
            for destination in row["destinations"]:
                if "artifact" in destination:
                    destination["artifact"] = str((previous_artifacts/destination["artifact"]).resolve())
            if row["source_table"] == "memory_graph_edges" and row["source_key"]["id"] in restored:
                ident = row["source_key"]["id"]
                current = dict(db.execute("SELECT * FROM memory_graph_edges WHERE id=?", (ident,)).fetchone())
                assert current == restored[ident]["corrected"]
                row["disposition"] = "transformed"
                row["destinations"].append({"table": "memory_graph_edges", "key": {"id": ident}, "transformation": "Native observation ref replaces malformed path wrapper; graph-audit.json contains exact before/after proof"})
                updated += 1
            counts[row["disposition"]] += 1
            target.write(json.dumps(row, ensure_ascii=False) + "\n")
    assert updated == len(restored)
    previous = json.loads((previous_artifacts/"record-accounting.json").read_text(encoding="utf-8"))
    assert sum(counts.values()) == previous["source_record_count"]
    result = {"source_record_count": sum(counts.values()), "mapped_record_count": sum(counts.values()), "disposition_counts": dict(counts), "restored_graph_mappings_verified": updated, "all_other_candidate_tables_verified_identical_to_previous_candidate": True, "prior_full_accounting": str((previous_artifacts/"record-accounting.json").resolve())}
    (artifacts/"record-accounting.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("original", "base", "quarantine", "output", "artifacts"):
        parser.add_argument("--"+arg, type=Path, required=True)
    args = parser.parse_args()
    args.artifacts.mkdir(parents=True, exist_ok=False)
    base_hash, original_hash = file_hash(args.base), file_hash(args.original)
    audit = graph_repair(args.original, args.base, args.output, json.loads(args.quarantine.read_text()))
    (args.artifacts/"graph-audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    accounting = carry_accounting(args.quarantine.parent, args.artifacts, audit, args.output)
    cleanup = CorpusCleanup(args.original, args.output)
    try:
        integrity = validate_database(cleanup)
    finally:
        cleanup.close()
    (args.artifacts/"integrity-validation.json").write_text(json.dumps(integrity, indent=2), encoding="utf-8")
    provider = asyncio.run(provider_checks(args.output, args.artifacts))
    (args.artifacts/"provider-validation.json").write_text(json.dumps(provider, ensure_ascii=False, indent=2), encoding="utf-8")
    # Native write/CAS checks are confined to a separate disposable copy.
    checks = asyncio.run(smoke(args.output, args.artifacts))
    (args.artifacts/"native-api-validation.json").write_text(json.dumps(checks, indent=2), encoding="utf-8")
    assert base_hash == file_hash(args.base) and original_hash == file_hash(args.original)
    report = {"original_sha256": original_hash, "previous_candidate_sha256": base_hash, "candidate_sha256": file_hash(args.output), "graph_counts": audit["counts"], "record_accounting": accounting, "integrity": integrity, "provider_tests_pass": provider["all_semantic_tests_pass"], "native_api_checks": checks, "production_comparison": "BLOCKED: saved SSH key rejected; no fresh live snapshot obtained", "production_cutover": "NOT PERFORMED"}
    (args.artifacts/"followup-results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
