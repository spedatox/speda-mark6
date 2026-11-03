"""Synthetic local retrieval benchmark; never a real-user accuracy evaluation."""
import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import sys
import tempfile
from time import perf_counter


async def measure(root: Path, scale: int, queries: int):
    from sqlalchemy import insert
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.core.context import AgentContext
    from app.database import Base
    import app.models
    from app.models.user import User
    from app.models.memory_serving import MemoryChange, MemoryServingState, MemoryServingUnit
    from app.services.memory_store import MemoryStore, digest, fold, init_indexes
    from app.services.memory_runtime import MemoryRuntime, packet_json
    engine = create_async_engine(f"sqlite+aiosqlite:///{root / f'benchmark-{scale}.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await init_indexes(conn)
    count, unit_count = 2000 * scale, 500 * scale
    sources, units = [], []
    for index in range(1, count + 1):
        name = f"Lumen{index:05}"
        body = f"{name} coordinates Project{index:05}. The owner reported that its planning decision is still pending."
        sources.append({"id":index,"user_id":1,"source_key":f"fixture:{index}",
            "source_role":"canonical","content":body,"content_hash":digest(body),
            "routing_text":fold(body),"outcome":"source_only","provenance":{}})
        if index <= unit_count:
            units.append({"user_id":1,"stable_key":f"person:{index}","kind":"person",
                "title":name,"aliases":[name],"revision":1,"routing_text":fold(body),
                "sections":[{"source_id":index,"start":0,"end":len(body),"text":body,
                    "attribution":"canonical_record"}],
                "dependencies":[{"source_id":index,"hash":digest(body)}]})
    async with sessions() as db, db.begin():
        db.add(User(id=1,name="Synthetic owner",timezone="UTC"))
        await db.flush()
        await db.execute(insert(MemoryChange), sources)
        await db.execute(insert(MemoryServingUnit), units)
        db.add(MemoryServingState(user_id=1,epoch=1))
    runtime = MemoryRuntime(MemoryStore(sessions))
    elapsed, matches, failures = [], 0, 0
    first = None
    for index in range(queries + 1):
        subject = index % 50 + 1
        name = f"Lumen{subject:05}"
        ctx = AgentContext(user_id=1,session_id=0,request_id=f"benchmark-{index}",
            triggered_by="user",trigger_payload={},output_mode="silent",model="unused",
            system_prompt="",conversation_history=[{"role":"user","content":f"How is {name} doing?"}],
            db=None,timezone="UTC")
        start = perf_counter()
        packet = await runtime.prepare(ctx)
        duration = (perf_counter() - start) * 1000
        if index == 0:
            first = duration
        else:
            elapsed.append(duration)
            matches += f"Project{subject:05}" in packet_json(packet)
            failures += packet.status in {"deadline","unavailable"}
    await engine.dispose()
    ordered = sorted(elapsed)
    return {"source_rows":count,"prepared_units":unit_count,"queries":queries,
        "first_reader_call_ms":round(first,3),
        "warm_p50_ms":round(ordered[math.ceil(len(ordered)*.5)-1],3),
        "warm_p95_ms":round(ordered[math.ceil(len(ordered)*.95)-1],3),
        "warm_p99_ms":round(ordered[math.ceil(len(ordered)*.99)-1],3),
        "warm_max_ms":round(max(ordered),3),"deadline_or_unavailable":failures,
        "exact_name_fixture_matches":matches,"memory_provider_calls":0}


async def main(args):
    scratch = Path(args.scratch).resolve()
    scratch.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="speda-benchmark-",dir=scratch) as directory:
        root = Path(directory)
        # Import defaults are forced to this disposable location; no configured DB.
        os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + (root / "unused.db").as_posix()
        os.environ["ANTHROPIC_API_KEY"] = "not-set"
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
        results = [await measure(root, scale, args.queries) for scale in (1,5)]
    report = {"kind":"synthetic_local_retrieval_benchmark","python":platform.python_version(),
        "platform":platform.platform(),"sqlalchemy":importlib.metadata.version("sqlalchemy"),
        "aiosqlite":importlib.metadata.version("aiosqlite"),"results":results,
        "limits":["Exact-name synthetic cases; not a held-out personal-memory accuracy result.",
            "No answering-model latency, semantic paraphrase coverage, OCR or production hardware measured.",
            "First call uses a cold reader cache in the seeding process, not an OS-cold restart."]}
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,indent=2),encoding="utf-8")
    sys.stdout.write(json.dumps(report,indent=2)+"\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",required=True)
    parser.add_argument("--queries",type=int,default=200)
    parser.add_argument("--scratch",default=str(Path.home()/".codex"/"tmp"))
    arguments = parser.parse_args()
    if arguments.queries < 20:
        parser.error("Use at least 20 queries")
    asyncio.run(main(arguments))
