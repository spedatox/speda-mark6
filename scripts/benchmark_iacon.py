"""Read-only local retrieval replay; no providers, credentials, or source text in output."""
import argparse
import asyncio
import json
from pathlib import Path
import statistics
import sys
from uuid import uuid4


async def run(args):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from sqlalchemy import event, select, func
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.core.context import AgentContext
    from app.models.iacon import IaconRecord, IaconFollowup
    from app.models.memory_serving import MemoryChange
    from app.services.memory_store import MemoryStore
    from app.services.memory_runtime import MemoryRuntime
    path = Path(args.database).resolve(strict=True)
    engine = create_async_engine("sqlite+aiosqlite:///" + path.as_posix())
    @event.listens_for(engine.sync_engine, "connect")
    def readonly(conn, _):
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=100")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    runtime = MemoryRuntime(MemoryStore(sessions))
    results = {"scope": "Offline retrieval only; no answering-model accuracy claim", "cases": [], "counts": {}}
    try:
        async with sessions() as db:
            for name, table in (("sources", MemoryChange), ("records", IaconRecord), ("followups", IaconFollowup)):
                results["counts"][name] = await db.scalar(select(func.count()).select_from(table))
        for label, query in (("named_person", "İlayda"), ("past_event", "Tuesday"),
                             ("current_states", "What is still waiting?"), ("preferences", "preferences")):
            timings, lookups, failures = [], [], 0
            for _ in range(25):
                ctx = AgentContext(user_id=1, session_id=0, request_id=uuid4().hex, triggered_by="user",
                    trigger_payload={}, output_mode="silent", model="unused", system_prompt="",
                    conversation_history=[{"role": "user", "content": query}], db=None, timezone="Europe/Istanbul")
                packet = await runtime.prepare(ctx)
                timings.append(packet.elapsed_ms)
                failures += packet.status in {"unavailable", "deadline"}
                extra = await runtime.lookup(ctx, query)
                lookups.append(extra.elapsed_ms)
                failures += extra.status in {"unavailable", "deadline"}
                assert ctx.memory_budget.used <= ctx.memory_budget.turn_limit
            results["cases"].append({"case": label, "prepare_p50_ms": round(statistics.median(timings), 2),
                "prepare_p95_ms": round(sorted(timings)[23], 2), "lookup_p95_ms": round(sorted(lookups)[23], 2),
                "failures": failures, "last_packet_evidence_count": len(packet.evidence_handles)})
        Path(args.report).write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(json.dumps(results, indent=2))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--report", required=True)
    asyncio.run(run(parser.parse_args()))
