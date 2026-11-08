"""Compare provider-free recall on a SQLite snapshot and its PostgreSQL rehearsal copy."""

import argparse
import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
from time import monotonic
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.core.context import AgentContext
from app.services.history_lookup import search_archive
from app.services.iacon_catalogue import Catalogue
from app.services.memory_runtime import MemoryRuntime, packet_json
from app.services.memory_store import MemoryStore
from app.skills.course_memory import ReadCourseMemorySkill


def context(case):
    return AgentContext(user_id=case.get("user_id", 1), session_id=0, request_id=uuid4().hex,
        triggered_by="user", trigger_payload={}, output_mode="silent", model="unused",
        system_prompt="", conversation_history=[{"role": "user", "content": case.get("query", "")}],
        db=None, timezone=case.get("timezone", "Europe/Istanbul"), agent_id=case.get("agent_id", "speda"))


def packet_result(packet):
    data = asdict(packet)
    for key in ("request_id", "elapsed_ms"):
        del data[key]
    data["model_payload"] = json.loads(packet_json(packet))
    return data


class FrozenRankingStore(MemoryStore):
    """Rehearsal control. Use SQLite's frozen ranks and hydrate from PostgreSQL."""

    def __init__(self, sessions, baseline):
        super().__init__(sessions)
        self.baseline = baseline

    async def _search(self, db, model, target, user_id, terms, limit, recent=False):
        async with self.baseline.reader() as original:
            ranked = await self.baseline._search(original, model, target, user_id, terms, limit, recent)
            ids = [row.id for row in ranked]
        if not ids:
            return []
        rows = await db.scalars(select(model).where(model.user_id == user_id, model.id.in_(ids)))
        by_id = {row.id: row for row in rows}
        return [by_id[identity] for identity in ids]


class Replay:
    def __init__(self, engine, *, frozen_ranking=None):
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)
        self.store = (FrozenRankingStore(self.sessions, frozen_ranking)
                      if frozen_ranking else MemoryStore(self.sessions))
        self.runtime = MemoryRuntime(self.store)
        self.courses = ReadCourseMemorySkill(Catalogue(self.store))

    async def run(self, case):
        ctx = context(case)
        started = monotonic()
        if case["mode"] == "personal":
            automatic = await self.runtime.prepare(ctx)
            lookup = await self.runtime.lookup(ctx, case["query"], source_ids=case.get("source_ids", []))
            values = {"automatic": packet_result(automatic), "lookup": packet_result(lookup)}
            timings = {"automatic": automatic.elapsed_ms, "lookup": lookup.elapsed_ms}
        else:
            async with self.sessions() as db:
                if case["mode"] == "history":
                    values = {"history": await search_archive(db, ctx, case["query"],
                        after=case.get("after"), before=case.get("before"), cursor=case.get("cursor"))}
                elif case["mode"] == "course":
                    ctx.db = db
                    values = {"course": json.loads(await self.courses.execute(case.get("args", {}), ctx))}
                else:
                    raise ValueError("Unknown replay mode")
            timings = {case["mode"]: (monotonic() - started) * 1000}
        return values, timings

    async def check(self, case):
        try:
            return await self.run(case)
        except Exception as error:
            surface = case["mode"]
            surfaces = ("automatic", "lookup") if surface == "personal" else (surface,)
            return {key: {"status": "error", "error_type": type(error).__name__} for key in surfaces}, {}


def handles(result):
    if "evidence_handles" in result:
        return result["evidence_handles"]
    if "results" in result:
        return [row["reference"] for row in result["results"]]
    if "records" in result:
        return [row["record_id"] for row in result["records"]]
    return [result["record_id"]] if "record_id" in result else []


async def compare(snapshot, url, cases, *, sqlite_ranking_control=False):
    snapshot = snapshot.resolve(strict=True)
    sqlite = create_async_engine("sqlite+aiosqlite:///" + snapshot.as_uri() + "?mode=ro&uri=true")
    postgres = create_async_engine(url, hide_parameters=True,
        connect_args={"server_settings": {"default_transaction_read_only": "on"}})
    baseline = Replay(sqlite)
    candidate = Replay(postgres, frozen_ranking=baseline.store if sqlite_ranking_control else None)
    summary = []
    try:
        if not sqlite_ranking_control:
            await candidate.store.warm_search()
        for case in cases:
            before, sqlite_ms = await baseline.check(case)
            retries = 0
            # A missed SQLite deadline can also change the following lookup's
            # evidence deduplication. Replay the whole case with a fresh turn.
            while retries < 2 and any(item.get("status") == "deadline" for item in before.values()):
                retries += 1
                before, sqlite_ms = await baseline.check(case)
            after, postgres_ms = await candidate.check(case)
            for key, expected in before.items():
                actual = after[key]
                original, migrated = handles(expected), handles(actual)
                summary.append({
                    "case": case["label"], "surface": key,
                    "sqlite_status": expected.get("status"), "postgres_status": actual.get("status"),
                    "sqlite_results": len(original), "postgres_results": len(migrated),
                    "missing_baseline_results": len(set(original) - set(migrated)),
                    "ranking_matches": original == migrated,
                    "evidence_matches": expected.get("blocks", expected.get("results", expected)) ==
                                        actual.get("blocks", actual.get("results", actual)),
                    "model_payload_matches": expected.get("model_payload", expected) ==
                                             actual.get("model_payload", actual),
                    "changed_fields": sorted(field for field in expected.keys() | actual.keys()
                                             if expected.get(field) != actual.get(field)),
                    "exact_result_matches": expected == actual and expected.get("status") not in {
                        "error", "unavailable", "deadline"},
                    "sqlite_deadline_retries": retries,
                    "sqlite_ms": round(sqlite_ms[key], 3) if key in sqlite_ms else None,
                    "postgres_ms": round(postgres_ms[key], 3) if key in postgres_ms else None,
                })
    finally:
        await sqlite.dispose()
        await postgres.dispose()
    return {"replay_results": summary,
            "candidate_ranking": "frozen SQLite control" if sqlite_ranking_control else "PostgreSQL-backed BM25",
            "exact_matches": sum(item["exact_result_matches"] for item in summary),
            "compared_results": len(summary),
            "cutover_ready": False,
            "remaining_gate": "Live Hall of Records access, ranking and citations require a separate rehearsal."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("cases", type=Path)
    parser.add_argument("report", type=Path)
    parser.add_argument("--sqlite-ranking-control", action="store_true",
                        help="For frozen copies only, isolate ranking differences from storage differences")
    parser.add_argument("--require-parity", action="store_true",
                        help="Exit with failure if any tested result differs")
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text(encoding="utf-8-sig"))
    report = asyncio.run(compare(args.snapshot, os.environ["SPEDA_REHEARSAL_POSTGRES_URL"], cases,
                                 sqlite_ranking_control=args.sqlite_ranking_control))
    with args.report.open("x", encoding="utf-8") as output:
        json.dump(report, output, indent=2)
    print(f'{report["exact_matches"]}/{report["compared_results"]} recall outputs match exactly.')
    if args.require_parity and report["exact_matches"] != report["compared_results"]:
        raise SystemExit(1)
