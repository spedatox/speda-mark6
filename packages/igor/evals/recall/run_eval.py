# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Recall eval harness — measures whether the roster can actually find a fact.

The premise of this harness is that "recall feels worse now" is not a diagnosis
and "recall feels better now" is not a result. Every change to the retrieval
path (the relevance floor, the fragment repair, the owner.md distillation, the
cross-lingual work) is scored against the SAME probe set before and after, and
the numbers are what decide whether the change shipped an improvement.

What it measures, per probe:

  * **hit@k** — did any of the top k retrieved observations contain the expected
    answer. hit@1 is the number that matters for a basic fact: an agent asked
    "when was he born" should not have to read five results to answer.
  * **MRR** — 1/rank of the first hit, 0 if none. Rewards ranking, not just
    presence, so a fix that merely drags the answer from rank 12 to rank 11 is
    visible but unimpressive.
  * **top score** — the fused score printed next to the rank-1 result. Tracked
    because a system that returns garbage at score 1.00 is worse than one that
    returns garbage at score 0.05 and can therefore be filtered.
  * **noise rate** — how often rank 1 is a hit-less result returned with a
    confident score. This is the failure the owner actually experiences.

Run it inside the app container, where the store and the OpenAI key live:

    docker cp evals/recall speda-app-1:/tmp/recall
    docker exec -w /app/packages/igor speda-app-1 \\
        /app/.venv/bin/python /tmp/recall/run_eval.py --user-id 1

Save a baseline before changing anything, then compare after:

    ... run_eval.py --save /tmp/before.json
    ... run_eval.py --compare /tmp/before.json

Probes live in probes.json next to this file. Matching is literal-substring and
ASCII-folded on both sides, so a probe may be written in plain ASCII and still
match stored Turkish text — see fold() below, which mirrors the folding the
lexical index itself uses (app/services/lexical.py).
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Importable both as a file copied into /tmp and from a checkout.
for candidate in ("/app/packages/igor", str(Path(__file__).resolve().parents[2])):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

PROBES = Path(__file__).with_name("probes.json")

# The same Turkish folding the lexical index applies, duplicated here on purpose:
# the harness must keep scoring even if it is run against a checkout where
# app.services.lexical has been changed, since that module is one of the things
# under test.
_FOLD = str.maketrans({
    "ı": "i", "İ": "i", "I": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
    "ç": "c", "Ç": "c", "ö": "o", "Ö": "o", "ü": "u", "Ü": "u",
})


def fold(value: str) -> str:
    return (value or "").translate(_FOLD).lower()


def _is_hit(text: str, expected: list[str]) -> bool:
    body = fold(text)
    return any(fold(e) in body for e in expected)


async def run_probes(user_id: int, limit: int, probe_file: Path) -> dict:
    from app.database import AsyncSessionLocal
    from app.services.observations import search_observations

    probes = json.loads(probe_file.read_text(encoding="utf-8"))["probes"]
    results = []

    async with AsyncSessionLocal() as db:
        for probe in probes:
            try:
                scored = await search_observations(
                    db, user_id=user_id, query=probe["query"], limit=limit
                )
            except Exception as e:  # noqa: BLE001
                results.append({
                    "id": probe["id"], "lang": probe["lang"], "tags": probe.get("tags", []),
                    "query": probe["query"], "error": str(e), "rank": None,
                    "top_score": 0.0, "top_text": "",
                })
                continue

            rank = None
            for i, (obs, _score) in enumerate(scored, start=1):
                if _is_hit(obs.content, probe["expect"]):
                    rank = i
                    break

            results.append({
                "id": probe["id"],
                "lang": probe["lang"],
                "tags": probe.get("tags", []),
                "query": probe["query"],
                "rank": rank,
                "returned": len(scored),
                "top_score": float(scored[0][1]) if scored else 0.0,
                "top_text": scored[0][0].content[:100] if scored else "",
                "hit_text": (
                    scored[rank - 1][0].content[:100] if rank else ""
                ),
            })

    return {"limit": limit, "results": results, "summary": summarise(results)}


def summarise(results: list[dict]) -> dict:
    def rate(rows, k):
        if not rows:
            return 0.0
        return sum(1 for r in rows if r["rank"] and r["rank"] <= k) / len(rows)

    def mrr(rows):
        if not rows:
            return 0.0
        return sum(1.0 / r["rank"] for r in rows if r["rank"]) / len(rows)

    tr = [r for r in results if r["lang"] == "tr"]
    en = [r for r in results if r["lang"] == "en"]
    # A "confident miss": rank 1 was not the answer, yet came back with a high
    # score. This is the number that corresponds to the owner's complaint.
    confident_misses = [
        r for r in results if r["rank"] != 1 and r["top_score"] >= 0.9
    ]

    return {
        "probes": len(results),
        "hit@1": round(rate(results, 1), 3),
        "hit@3": round(rate(results, 3), 3),
        "hit@5": round(rate(results, 5), 3),
        "hit@15": round(rate(results, 15), 3),
        "mrr": round(mrr(results), 3),
        "hit@5_en": round(rate(en, 5), 3),
        "hit@5_tr": round(rate(tr, 5), 3),
        "confident_miss_rate": round(len(confident_misses) / len(results), 3) if results else 0.0,
    }


def print_report(report: dict, verbose: bool) -> None:
    s = report["summary"]
    print("\n── Recall eval ────────────────────────────────────────────")
    print(f"  probes            {s['probes']}")
    print(f"  hit@1             {s['hit@1']:.1%}")
    print(f"  hit@3             {s['hit@3']:.1%}")
    print(f"  hit@5             {s['hit@5']:.1%}   (en {s['hit@5_en']:.1%} · tr {s['hit@5_tr']:.1%})")
    print(f"  hit@{report['limit']:<13} {s['hit@15']:.1%}")
    print(f"  MRR               {s['mrr']:.3f}")
    print(f"  confident misses  {s['confident_miss_rate']:.1%}  (rank-1 wrong, score ≥ 0.90)")

    misses = [r for r in report["results"] if not r["rank"]]
    if misses:
        print(f"\n  {len(misses)} probe(s) with NO hit in top {report['limit']}:")
        for r in misses:
            print(f"    ✗ [{r['lang']}] {r['query']}")
            if verbose:
                print(f"        rank1 ({r['top_score']:.2f}): {r['top_text']}")
    if verbose:
        hits = [r for r in report["results"] if r["rank"]]
        print(f"\n  {len(hits)} hit(s):")
        for r in sorted(hits, key=lambda r: r["rank"]):
            print(f"    ✓ @{r['rank']:<2} [{r['lang']}] {r['query']}")
            print(f"        {r['hit_text']}")
    print()


def print_diff(before: dict, after: dict) -> None:
    b, a = before["summary"], after["summary"]
    print("\n── Recall eval · before → after ───────────────────────────")
    for key in ("hit@1", "hit@3", "hit@5", "hit@15", "mrr", "hit@5_en", "hit@5_tr", "confident_miss_rate"):
        delta = a[key] - b[key]
        arrow = "▲" if delta > 0 else ("▼" if delta < 0 else " ")
        # A fall in confident misses is an improvement, so the arrow inverts.
        if key == "confident_miss_rate":
            arrow = "▲" if delta < 0 else ("▼" if delta > 0 else " ")
        print(f"  {key:<20} {b[key]:>6.3f} → {a[key]:>6.3f}   {arrow} {delta:+.3f}")

    by_id = {r["id"]: r for r in before["results"]}
    fixed, broke = [], []
    for r in after["results"]:
        old = by_id.get(r["id"])
        if not old:
            continue
        if not old["rank"] and r["rank"]:
            fixed.append((r, old))
        elif old["rank"] and not r["rank"]:
            broke.append((r, old))
    if fixed:
        print(f"\n  fixed ({len(fixed)}):")
        for r, _ in fixed:
            print(f"    ✓ [{r['lang']}] {r['query']}  → rank {r['rank']}")
    if broke:
        print(f"\n  REGRESSED ({len(broke)}):")
        for r, old in broke:
            print(f"    ✗ [{r['lang']}] {r['query']}  (was rank {old['rank']})")
    print()


def main() -> None:
    p = argparse.ArgumentParser(description="Score observation recall against a fixed probe set.")
    p.add_argument("--user-id", type=int, default=1)
    p.add_argument("--limit", type=int, default=15, help="Retrieval depth (the deepest hit@k reported).")
    p.add_argument("--probes", type=Path, default=PROBES)
    p.add_argument("--save", type=Path, help="Write the full report here (use as a baseline).")
    p.add_argument("--compare", type=Path, help="Diff this run against a saved baseline.")
    p.add_argument("-v", "--verbose", action="store_true", help="Show every probe, not just misses.")
    args = p.parse_args()

    report = asyncio.run(run_probes(args.user_id, args.limit, args.probes))

    if args.compare:
        print_diff(json.loads(args.compare.read_text(encoding="utf-8")), report)
    else:
        print_report(report, args.verbose)

    if args.save:
        args.save.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  baseline written to {args.save}\n")


if __name__ == "__main__":
    main()
