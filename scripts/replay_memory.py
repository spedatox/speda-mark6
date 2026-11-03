"""Read-only real-snapshot memory replay. Writes private evidence only to output."""
import argparse
import asyncio
import json
import math
import os
from pathlib import Path
import sys


async def main(args):
    database = Path(args.database).resolve(strict=True)
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + database.as_posix()
    os.environ["ANTHROPIC_API_KEY"] = "not-set"
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from app.core.context import AgentContext
    from app.database import AsyncSessionLocal,close_db
    from app.services.memory_store import MemoryStore
    from app.services.memory_runtime import MemoryRuntime,packet_json
    runtime = MemoryRuntime(MemoryStore(AsyncSessionLocal))
    cases = [
        ("named_people", "Ilayda and Erdem are paying us a visit.", ""),
        ("relationship", "Gather context about Ilayda and our current relationship.", ""),
        ("available_image_text_cue", "Mister and mrs. perfecr all-mighty paying us a visit. Great.", "İlayda"),
        ("uncached_image_no_text_cue", "Mister and mrs. perfecr all-mighty paying us a visit. Great.", "")]
    packets=[]
    timings=[]
    lookup_timings=[]
    try:
        for repeat in range(args.repeats):
            for name,query,cue in cases:
                context=AgentContext(user_id=1,session_id=0,request_id=f"replay-{repeat}-{name}",
                    triggered_by="user",trigger_payload={},output_mode="silent",model="unused",
                    system_prompt="",conversation_history=[{"role":"user","content":query}],
                    db=None,timezone="Europe/Istanbul",extra={"attachment_text":cue})
                packet=await runtime.prepare(context)
                fallback=None
                if name=="uncached_image_no_text_cue":
                    # The lookup query uses names visible in the supplied incident
                    # image. This tests serving after vision resolves them, not OCR.
                    fallback=await runtime.lookup(context,"Ilayda and Erdem current relationship")
                if repeat==0:
                    packets.append({"case":name,"diagnostics":packet.diagnostics(),
                                    "packet":json.loads(packet_json(packet)),
                                    "fallback":json.loads(packet_json(fallback)) if fallback else None})
                else:
                    timings.append(packet.elapsed_ms)
                    if fallback:
                        lookup_timings.append(fallback.elapsed_ms)
        private=Path(args.output).resolve()
        private.parent.mkdir(parents=True,exist_ok=True)
        private.write_text(json.dumps(packets,ensure_ascii=False,indent=2),encoding="utf-8")
        ordered=sorted(timings)
        report={"scope":"Real offline snapshot, four targeted memory-selection cases; no answering model",
            "cases":[{"case":r["case"],**r["diagnostics"]} for r in packets],
            "warm_reads":len(timings),"warm_p95_ms":round(ordered[math.ceil(len(ordered)*.95)-1],3) if ordered else None,
            "warm_max_ms":round(max(ordered),3) if ordered else None,
            "warm_lookup_p95_ms":round(sorted(lookup_timings)[math.ceil(len(lookup_timings)*.95)-1],3) if lookup_timings else None,
            "memory_provider_calls":0,
            "limits":["Available-image-text-cue condition supplies a known visible name; OCR was not measured.",
                "Uncached images retain native vision and may require one lookup; no vision model was called.",
                "These cases do not establish general 90% tool avoidance or first-answer accuracy."]}
        Path(args.report).resolve().write_text(json.dumps(report,indent=2),encoding="utf-8")
        sys.stdout.write(json.dumps(report,indent=2)+"\n")
    finally:
        await close_db()


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database",required=True)
    parser.add_argument("--output",required=True)
    parser.add_argument("--report",required=True)
    parser.add_argument("--repeats",type=int,default=26)
    asyncio.run(main(parser.parse_args()))
