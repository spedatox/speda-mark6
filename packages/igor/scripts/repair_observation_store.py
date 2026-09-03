# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
One-off repair of the observation store: fragments → facts, Turkish → English.

## Why

Recall degraded as memory grew, and the cause was not the retriever. It was the
store. Measured on 2026-09-03 against 638 live observations:

  * **60% were fragments, not facts.** 545 rows carry `origin="seed"` — they were
    parsed out of the pre-v3 markdown files by splitting them line by line, so the
    store inherited bullets rather than claims: "a table of incomes",
    "**Started:** 2026-08-01", "Keep entries dated by month.", "Concise, direct
    responses with minimal fluff". Only 109 of 638 rows named their subject at
    all.
  * That is a RETRIEVAL bug, not an untidiness. A sentence with no subject has no
    distinctive meaning, so its embedding lands near the centre of the space —
    close to every query. Fragments therefore occupied the top of the ranking for
    questions they had no bearing on, and the more of them accumulated, the more
    of the top-k they took from real facts. "The more memory grew, the harder
    basic recall became" is the exact behaviour this predicts.
  * **The basics were never distilled at all.** No observation contained the
    owner's birth date, home coordinates or GPA — those live only as prose inside
    owner.md, which is injected but never retrieved. See
    `distill_memory_files.py`, which is the other half of this repair.
  * **Nothing had an end date.** 1,758 live rows carried no `valid_until`, so a
    job he left in 2023 was as current as the one he holds. That defect has its
    own pass here — see `backfill_validity()` and `--validity`.
  * **70 rows were Turkish** in an otherwise English store. A mixed-language
    corpus splits every concept across two embedding neighbourhoods and halves
    the lexical index's usefulness; the owner's decision is that the store is
    English, and queries may be asked in either language.

## What it does

For every live observation, in one pass:

  1. `fragment_reason()` (app/services/observations.py) decides whether the row
     is a self-contained fact. Rows that pass AND are already English are left
     completely alone — this is why the script is safe to re-run.
  2. Everything else goes to the model in batches, with its siblings as context.
     Sibling context matters: "a table of incomes" is meaningless alone but
     obviously part of a finance-report spec when shown next to "a table of
     expenses" and "Keep entries dated by month.", and the model can only write
     the real fact if it can see that.
  3. The model returns, per row, either a rewritten self-contained English
     sentence or DROP — the honest answer for a row that never carried a fact
     ("Use clear totals for each table" is an instruction to a renderer, not
     something true about the owner).
  4. Rewrites are re-validated through `fragment_reason()` before they are
     accepted. A repair that produces another fragment is refused and the row is
     left for a human, not written back.
  5. Accepted rewrites are re-embedded and re-indexed in FTS5 so both halves of
     recall see the new text. DROPs are SOFT-deleted — `deleted_at`, never a
     DELETE, matching soft_delete_observations() — owner knowledge is
     demoted out of recall, never destroyed.

## Running it

Always dry-run first; it prints every proposed change and writes nothing:

    docker cp scripts/repair_observation_store.py speda-app-1:/tmp/
    docker exec -w /app/packages/igor speda-app-1 \\
        /app/.venv/bin/python /tmp/repair_observation_store.py --user-id 1 --dry-run

Then commit, backing the database up first (WAL mode makes `cp` unsafe, so this
uses SQLite's own backup API):

    docker exec -w /app/packages/igor speda-app-1 \\
        /app/.venv/bin/python /tmp/repair_observation_store.py --user-id 1 --commit

`--limit N` processes only the first N repairable rows, which is the sane way to
check the prompt is behaving before spending a few hundred model calls on it.

`--validity` runs the OTHER repair instead: end-dating facts that have ended, so
that a question about the present stops being answered with the past.
"""

import argparse
import asyncio
import json
import logging
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

for candidate in ("/app/packages/igor", str(Path(__file__).resolve().parents[1])):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

logging.basicConfig(level=logging.WARNING, format="%(message)s")
log = logging.getLogger("repair")

BATCH_SIZE = 8           # rows per model call — 12 overran the token budget and returned nothing
SIBLING_CONTEXT = 6      # neighbouring rows shown alongside a batch for orientation

# Language detection by FUNCTION WORDS, not by alphabet. The obvious test —
# "does it contain ı, ğ, ş" — flags every English row that mentions Uludağ,
# OSTİM, Akçalar or Arel Tarım, which is most of them: it marked 589 of 638 rows
# Turkish on the first run. Proper nouns keep their spelling in both languages,
# so only the grammar words separate the two.
_TR_WORDS = re.compile(
    r"\b(ve|bir|için|ile|olarak|değil|kadar|sonra|olan|olduğu|bu|şu|ama|çok|"
    r"var|yok|ise|veya|ancak|daha|gibi|üzere|göre|her|kendi|başka|sadece|"
    r"tarafından|hakkında|arasında|önce|birlikte)\b",
    re.IGNORECASE,
)
_EN_WORDS = re.compile(
    r"\b(the|and|for|with|that|this|was|were|are|is|has|have|had|from|his|her|"
    r"their|of|to|in|on|at|by|as|an|a|not|but|he|she|it)\b",
    re.IGNORECASE,
)


def looks_turkish(text: str) -> bool:
    """Whether a row is written in Turkish and so needs translating.

    Compares how many Turkish grammar words appear against how many English
    ones. A row is Turkish when its Turkish function words outnumber its English
    ones — which is robust to the Turkish place and institution names that
    legitimately appear in English facts, and to the English loanwords that
    appear in Turkish ones.
    """
    body = text or ""
    tr, en = len(_TR_WORDS.findall(body)), len(_EN_WORDS.findall(body))
    if tr > en:
        return True
    # A short Turkish clause can carry no function word at all ("Ortak Dersler
    # Bölüm Başkanlığı bildirdi"). Turkish-only letters with no English grammar
    # around them is the remaining signal.
    return en == 0 and bool(re.search(r"[ığşİĞŞçöü]", body))


_PROMPT = """\
You are repairing an owner's long-term memory store. Each entry below was
scraped out of a markdown file by splitting it line by line, so most are
FRAGMENTS — bullets, field labels, or headings — rather than facts.

Rewrite each entry as ONE self-contained English sentence that would still
answer a question if someone read it alone, years from now, with no other
context. Rules:

- ENGLISH ONLY. Translate Turkish entries. Keep proper nouns, place names,
  institution names, course codes and currency codes in their original form
  (Uludağ, OSTİM, TL, Akçalar) — translate the sentence, not the names.
- NAME THE SUBJECT. Never "Employed at X" — write "Ahmet Erol Bayrak is
  employed at X". Never "a table of incomes" — say what is actually true.
- KEEP EVERY SPECIFIC. Numbers, dates, names, amounts and identifiers are the
  whole value of the entry. Never round, never generalise, never drop one.
- INVENT NOTHING. Use only what the entry and its context actually say. If a
  detail is not there, leave it out rather than guessing.
- Under 300 characters.

Answer DROP instead of a sentence when the entry carries no durable fact about
the owner or his world — a formatting instruction ("Use clear totals for each
table"), a section heading, a template placeholder, or a bare label whose
meaning cannot be recovered from the context given.

CONTEXT — nearby entries from the same source, for orientation only. Do NOT
rewrite these:
{context}

ENTRIES TO REWRITE:
{entries}

Return ONLY a JSON array, one object per entry, in the same order:
[{{"n": 1, "text": "<the rewritten sentence>"}}, {{"n": 2, "text": "DROP"}}]
"""


def _parse_json_array(raw: str) -> list[dict]:
    """Pull the JSON array out of a model response, fenced or not."""
    body = (raw or "").strip()
    if body.startswith("```"):
        body = re.sub(r"^```[a-z]*\s*", "", body)
        body = re.sub(r"\s*```$", "", body)
    start, end = body.find("["), body.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        parsed = json.loads(body[start : end + 1])
    except json.JSONDecodeError:
        return []
    return [p for p in parsed if isinstance(p, dict)]


async def repair_batch(client, model: str, batch: list, context: list) -> dict[int, str]:
    """Ask the model to repair one batch. Returns {observation_id: new_text|DROP}."""
    entries = "\n".join(
        f"{i}. [{o.domain} · {o.subject} · {o.created_at:%Y-%m-%d}] {o.content}"
        for i, o in enumerate(batch, start=1)
    )
    ctx = "\n".join(f"- {c.content[:160]}" for c in context) or "(none)"

    resp = await client.create_message(
        model=model,
        system="You repair memory records. You return only the JSON array asked for.",
        messages=[{"role": "user", "content": _PROMPT.format(context=ctx, entries=entries)}],
        max_tokens=8192,
        # Reasoning models otherwise spend the entire budget thinking and
        # return an empty message — the same trap app/services/memory.py
        # documents for title generation. This is extraction, not
        # reasoning; it needs the tokens for output.
        reasoning_effort="minimal",
    )
    raw = resp.content[0].text if resp.content else ""

    out: dict[int, str] = {}
    for item in _parse_json_array(raw):
        try:
            n = int(item.get("n", 0))
        except (TypeError, ValueError):
            continue
        text = str(item.get("text") or "").strip()
        if 1 <= n <= len(batch) and text:
            out[batch[n - 1].id] = text
    return out


def backup_database() -> str | None:
    """Snapshot the SQLite store through its own backup API (WAL-safe)."""
    from app.config import settings

    url = settings.database_url
    if not url.startswith("sqlite"):
        log.warning("! not SQLite — skipping backup, rely on your own snapshot")
        return None
    src = url.split("///")[-1]
    dst = f"{src}.repair-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.bak"
    source = sqlite3.connect(src)
    target = sqlite3.connect(dst)
    with target:
        source.backup(target)
    source.close()
    target.close()
    return dst


# ── Validity backfill ─────────────────────────────────────────────────────────
# A separate pass, because it repairs a different defect from the fragment one.
#
# 1,758 live observations carried no `valid_until`, which means the store could
# not tell a job he holds from a job he left in 2023. "Where does he work right
# now" returned Nettech — two weeks of sales, three years ago — because sixteen
# past jobs and one present one were all equally current, and there were more of
# the past ones. `current_only` on search_memory cannot help while the data
# insists everything is still true.
#
# Only rows that MENTION A YEAR are considered. A fact with no date in it is
# usually durable ("Osman Bayrak is Ahmet Erol's father") and asking a model to
# rule on its expiry invites exactly the confident guess this store does not
# need. The narrowing takes 1,758 candidates down to 653, and removes most of
# the risk at very little cost to the benefit.

_VALIDITY_PROMPT = """\
Each entry below is a recorded fact about the owner or his world. For each one,
decide whether it describes something that has ENDED.

Answer with the end date, "YYYY-MM-DD", ONLY when the entry itself says the
thing finished — a job with a stated last month, a period given as a range, an
event that happened on a date and is over. If the entry gives only a month or a
year for the ending, use the last day of it ("August 2026" becomes 2026-08-31).

Answer NONE when:
  - the entry is still true, or describes something ongoing;
  - the entry is a durable fact that cannot end — a birth date, a parent, a
    permanent trait, a completed exam score;
  - the entry does not actually say when it ended. A date it MENTIONS is not
    necessarily the date it ENDED.

When in doubt answer NONE. Marking a live fact as ended hides it from every
question about the present, which is a worse error than leaving it unmarked.

ENTRIES:
{entries}

Return ONLY a JSON array, one object per entry, in the same order:
[{{"n": 1, "end": "2026-08-31"}}, {{"n": 2, "end": "NONE"}}]
"""

_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


async def backfill_validity(args, model: str) -> None:
    """Give ended facts an end date, so the present can outrank the past."""
    from datetime import date

    from app.database import AsyncSessionLocal
    from app.models.observation import Observation
    from app.services.llm_client import LLMClient
    from app.services.observations import _live

    async with AsyncSessionLocal() as db:
        rows = [
            o for o in (await db.execute(
                _live(args.user_id).where(Observation.valid_until.is_(None))
            )).scalars().all()
            if _YEAR.search(o.content)
        ]

    print(f"\n  {len(rows)} live fact(s) with no end date that mention a year")
    if args.limit:
        rows = rows[: args.limit]
        print(f"  --limit {args.limit}: processing {len(rows)}")
    if not rows:
        return

    backup = backup_database() if args.commit else None
    if backup:
        print(f"  backup: {backup}")

    client = LLMClient()
    decided: dict[int, str] = {}
    batches = -(-len(rows) // BATCH_SIZE)
    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start : start + BATCH_SIZE]
        entries = "\n".join(
            f"{i}. [{o.domain}] {o.content}" for i, o in enumerate(batch, start=1)
        )
        print(f"  ... batch {start // BATCH_SIZE + 1}/{batches}", flush=True)
        try:
            resp = await client.create_message(
                model=model,
                system=(
                    "You judge whether a fact has ended. You return only the "
                    "JSON array asked for."
                ),
                messages=[{
                    "role": "user",
                    "content": _VALIDITY_PROMPT.format(entries=entries),
                }],
                max_tokens=4096,
                reasoning_effort="minimal",
            )
        except Exception as e:  # noqa: BLE001
            log.warning("  ! batch failed: %s", e)
            continue
        raw = resp.content[0].text if resp.content else ""
        for item in _parse_json_array(raw):
            try:
                n = int(item.get("n", 0))
            except (TypeError, ValueError):
                continue
            end = str(item.get("end") or "").strip()
            if 1 <= n <= len(batch) and _ISO_DAY.fullmatch(end):
                decided[batch[n - 1].id] = end

    print(f"\n  {len(decided)} fact(s) to be marked as ended\n")
    by_id = {o.id: o for o in rows}
    for oid, end in list(decided.items())[:80]:
        print(f"  [{oid}] ends {end}: {by_id[oid].content[:110]}")

    if args.dry_run:
        print("\n  Dry run - nothing written.\n")
        return
    if not decided:
        return

    async with AsyncSessionLocal() as db:
        fresh = (await db.execute(
            _live(args.user_id).where(Observation.id.in_(list(decided)))
        )).scalars().all()
        applied = 0
        for obs in fresh:
            try:
                obs.valid_until = date.fromisoformat(decided[obs.id])
                applied += 1
            except ValueError:
                continue
        await db.commit()
    print(f"\n  Marked {applied} fact(s) as ended.\n")


async def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--user-id", type=int, default=1)
    p.add_argument("--limit", type=int, help="Only process the first N repairable rows.")
    p.add_argument("--model", default="", help="Override the repair model (default: background model).")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Print every change, write nothing.")
    mode.add_argument("--commit", action="store_true", help="Apply the repairs.")
    p.add_argument("--validity", action="store_true",
                   help="Backfill end dates instead of repairing fragments. See backfill_validity().")
    args = p.parse_args()

    from app.config import settings
    from app.database import AsyncSessionLocal
    from app.services import lexical
    from app.services.embeddings import embed_texts
    from app.models.observation import Observation
    from app.services.observations import _live, fragment_reason
    from app.services.llm_client import LLMClient

    model = args.model or settings.llm_background_model or settings.llm_main_model
    if not model:
        print("No model configured (llm_background_model / llm_main_model).")
        return

    if args.validity:
        await backfill_validity(args, model)
        return

    async with AsyncSessionLocal() as db:
        rows = list((await db.execute(_live(args.user_id).order_by("id"))).scalars().all())

    # Partition. `clean` is the whole point of the idempotence guarantee: a row
    # that is already a self-contained English fact is never sent to the model,
    # so a second run of this script is nearly free and changes nothing.
    repairable, clean = [], []
    for o in rows:
        if fragment_reason(o.content) is not None or looks_turkish(o.content):
            repairable.append(o)
        else:
            clean.append(o)

    print(f"\n  {len(rows)} live observations")
    print(f"  {len(clean)} already self-contained English facts — untouched")
    print(f"  {len(repairable)} to repair ({sum(1 for o in repairable if looks_turkish(o.content))} Turkish)")

    if args.limit:
        repairable = repairable[: args.limit]
        print(f"  --limit {args.limit}: processing {len(repairable)}")
    if not repairable:
        print("\n  Nothing to do.\n")
        return

    backup = None
    if args.commit:
        backup = backup_database()
        print(f"  backup: {backup}" if backup else "  backup: skipped")

    client = LLMClient()
    rewritten, dropped, refused, failed = [], [], [], []

    for start in range(0, len(repairable), BATCH_SIZE):
        batch = repairable[start : start + BATCH_SIZE]
        # Siblings come from the surrounding ids, which is where line-by-line
        # seeding put the rest of the source document.
        context = repairable[max(0, start - SIBLING_CONTEXT) : start] + \
                  repairable[start + len(batch) : start + len(batch) + SIBLING_CONTEXT]
        print(f"  … batch {start // BATCH_SIZE + 1}/{-(-len(repairable) // BATCH_SIZE)}", flush=True)
        try:
            repairs = await repair_batch(client, model, batch, context)
        except Exception as e:  # noqa: BLE001
            log.warning("  ! batch failed: %s", e)
            failed.extend(batch)
            continue

        for obs in batch:
            new = repairs.get(obs.id)
            if not new:
                failed.append(obs)
            elif new.upper().strip(".") == "DROP":
                dropped.append(obs)
            else:
                # A repair that is still a fragment is not a repair. Refusing it
                # here is what stops this script from laundering bad rows into
                # the store with the guard's own blessing.
                reason = fragment_reason(new)
                if reason is not None:
                    refused.append((obs, new, reason))
                else:
                    rewritten.append((obs, new))

    print("\n── Proposed ──────────────────────────────────────────────")
    print(f"  rewrite  {len(rewritten)}")
    print(f"  drop     {len(dropped)}")
    print(f"  refused  {len(refused)}  (model's rewrite was still a fragment)")
    print(f"  failed   {len(failed)}   (no response — safe to re-run)")

    for obs, new in rewritten[:400]:
        print(f"\n  [{obs.id}] {obs.content[:110]}\n     → {new[:150]}")
    for obs in dropped[:120]:
        print(f"\n  [{obs.id}] DROP: {obs.content[:110]}")
    for obs, new, reason in refused[:40]:
        print(f"\n  [{obs.id}] REFUSED ({reason[:60]}): {new[:100]}")

    if args.dry_run:
        print("\n  Dry run — nothing written.\n")
        return

    # ── Apply ────────────────────────────────────────────────────────────────
    async with AsyncSessionLocal() as db:
        ids = [o.id for o, _ in rewritten] + [o.id for o in dropped]
        live = {}
        if ids:
            # Re-read inside the write session rather than reusing the detached
            # instances from the read pass: the rows may have moved since, and a
            # repair must never resurrect a fact demoted while it was running.
            fresh = (
                await db.execute(_live(args.user_id).where(Observation.id.in_(ids)))
            ).scalars().all()
            live = {o.id: o for o in fresh}

        texts = [new for _, new in rewritten]
        vectors = []
        for i in range(0, len(texts), 64):
            try:
                vectors.extend(await embed_texts(texts[i : i + 64]))
            except Exception as e:  # noqa: BLE001
                log.warning("  ! embed batch failed (%s) — those rows keep their old vector", e)
                vectors.extend([None] * len(texts[i : i + 64]))

        now = datetime.now(timezone.utc)
        for (obs, new), vec in zip(rewritten, vectors):
            row = live.get(obs.id)
            if row is None:
                continue
            row.content = new
            row.updated_at = now
            if vec is not None:
                row.embedding = vec.tobytes()
            await lexical.index_observation(db, row)

        for obs in dropped:
            row = live.get(obs.id)
            if row is None:
                continue
            row.deleted_at = now
            await lexical.drop_row(db, lexical.OBSERVATIONS, row.id)

        await db.commit()

    print(f"\n  Committed: {len(rewritten)} rewritten, {len(dropped)} demoted.")
    if backup:
        print(f"  Backup at {backup}\n")


if __name__ == "__main__":
    asyncio.run(main())
