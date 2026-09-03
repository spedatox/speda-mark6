# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Distil the narrative memory files into retrievable facts.

## Why

owner.md is 13.8 KB of biography — a life story, well written, injected into
every single system prompt. It is also the ONLY place a large number of basic
facts exist. Measured on 2026-09-03: no observation in the store contained the
owner's birth date, his home coordinates, his Uludağ GPA, his YDS score or which
phone he carries. Every one of those is in owner.md, buried mid-paragraph.

That produces the exact failure the owner reported — "I have to explain the most
simple things now":

  * The narrative is INJECTED but never RETRIEVED. `search_memory` searches
    observations; owner.md is not in that index, so recall cannot find a fact
    that only lives there.
  * Injection does not scale as attention. A fact stated once inside a 13.8 KB
    story, inside a 28.7 KB memory block, competes with everything else in the
    prompt. It was reliably found when the block was small. It is not now.
  * And the store's growth made it worse rather than better, because what grew
    was project and training minutiae (225 + 191 observations) while biography
    stayed at 41. Recall got louder without getting more knowledgeable.

The fix is not to delete the narrative — it is genuinely good context and the
owner should keep it. The fix is that every ATOMIC fact inside it also exists as
an observation, so it can be retrieved by a question rather than only spotted by
a careful reader.

## What it does

  1. Reads the narrative files (owner.md by default) and asks the model to
     extract every atomic, durable fact as one self-contained English sentence,
     with a domain and a subject.
  2. Every candidate is validated through the same `validate_observation` the
     live write path uses — including the fragment guard — so distillation
     cannot inject anything the roster would be forbidden from recording itself.
  3. Deduplicates against the existing store on two levels: exact normalised
     content, and cosine similarity above `--dup-threshold` (0.93 by default)
     against what is already recorded. This is what makes the script re-runnable
     as the narrative grows: a second run adds only what the first did not.
  4. Writes the survivors as `origin="seed"`, `observer="owner"`, embedded and
     FTS-indexed, which is what puts them inside `search_memory`'s reach.

Nothing in the source files is modified or deleted. This is purely additive.

## Running it

    docker cp scripts/distill_owner_profile.py speda-app-1:/tmp/
    docker exec -w /app/packages/igor speda-app-1 \\
        /app/.venv/bin/python /tmp/distill_owner_profile.py --user-id 1 --dry-run

    ... /tmp/distill_owner_profile.py --user-id 1 --commit

Pass `--path` to distil a different narrative file (repeatable).
"""

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

for candidate in ("/app/packages/igor", str(Path(__file__).resolve().parents[1])):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

DEFAULT_PATHS = ["/memories/owner.md"]

# Chunk the narrative by section so each extraction call sees a coherent episode
# with its own dates and names, rather than a window cut mid-paragraph.
SECTION = re.compile(r"^##\s+", re.MULTILINE)
MAX_SECTION_CHARS = 6000

# A section shorter than this has no facts in it — it is a title or a stub. This
# is not a tidiness threshold, it is a SAFETY one. On the first run the opening
# chunk of owner.md was just "# Owner Profile" plus a dossier title, and handed
# that, the model did not return an empty array: it invented an entire plausible
# biography (a birthplace in Eskişehir, a father with a textile shop, a high
# school, a barista job, Spring Boot). Fabricated biography written into the
# owner's memory is far worse than the recall bug this whole exercise is fixing,
# so starved sections are never sent at all.
MIN_SECTION_CHARS = 240

# ── The grounding check ───────────────────────────────────────────────────────
# The prompt says INVENT NOTHING; this is what enforces it, because a prompt
# instruction is a request and this is a test. Every number and every proper noun
# in an extracted fact must actually occur in the section it was extracted from.
# Those are precisely the tokens a fabrication introduces — "Eskişehir",
# "Bulgaria", "3.17", "2022" — and precisely the tokens that carry the fact's
# value, so checking them costs nothing and catches confabulation cheaply.
_NUMBERS = re.compile(r"\d[\d.,:/]*\d|\d")
_PROPER = re.compile(r"\b[A-ZÇĞİÖŞÜ][\w'’-]{2,}\b")

# Words that start a sentence or are simply common in English prose. A proper
# noun this common is not evidence of anything, and demanding it appear in the
# source would reject sound facts over the word "The".
_PROPER_STOP = frozenset({
    "the", "he", "his", "him", "she", "her", "they", "their", "it", "its",
    "this", "that", "these", "those", "and", "but", "for", "with", "from",
    "after", "before", "during", "between", "when", "while", "because",
    "ahmet", "erol", "bayrak", "owner", "both", "one", "two", "three",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december", "turkish", "english",
    # Generic nouns a rewrite legitimately ADDS when it expands a name into a
    # sentence: a source saying "Uludag" becomes "Uludag University", and the
    # added word is not evidence of invention. Kept deliberately short — every
    # entry here is a hole in the check.
    "university", "universitesi", "college", "school", "hospital", "company",
    "foundation", "faculty", "program", "programme", "exam", "protocol",
    "dormitory", "scholarship", "degree", "internship", "region", "street",
})


def _base(token: str) -> str:
    """Strip the English possessive so "Bayrak's" is checked as "Bayrak".

    A rewrite turns "his father" into "Ahmet Erol Bayrak's father"; the
    apostrophe is grammar the rewrite added, not a token the source failed to
    contain, and treating it as invention rejects sound facts wholesale.
    """
    return re.sub(r"['’]s$", "", token)


def _fold(value: str) -> str:
    return (value or "").translate(str.maketrans({
        "ı": "i", "İ": "i", "I": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
        "ç": "c", "Ç": "c", "ö": "o", "Ö": "o", "ü": "u", "Ü": "u",
    })).lower()


def ungrounded_tokens(fact: str, source: str) -> list[str]:
    """Tokens in `fact` that do not appear in `source`. Empty means grounded.

    Folded on both sides so a fact written "Uludag" still matches a source that
    says "Uludağ" — the check is for invention, not for spelling drift.
    """
    haystack = _fold(source)
    missing: list[str] = []
    for token in _NUMBERS.findall(fact):
        # Numbers are compared with separators stripped: a source "10,000 TL"
        # legitimately becomes "10000 TL" in a rewritten sentence.
        plain = re.sub(r"[.,:/]", "", token)
        if token not in source and plain not in re.sub(r"[.,:/]", "", source):
            missing.append(token)
    for token in _PROPER.findall(fact):
        folded = _fold(_base(token))
        if folded in _PROPER_STOP:
            continue
        if folded not in haystack:
            missing.append(token)
    return missing

_PROMPT = """\
Below is one section of a biographical profile of Ahmet Erol Bayrak, written as
narrative prose. Extract every ATOMIC, DURABLE fact it states.

An atomic fact is ONE self-contained English sentence that answers a question on
its own, read years later with none of this text around it. Rules:

- NAME HIM. "Ahmet Erol Bayrak was born on 18 October 2004", never "Born in
  2004". The sentence is stored alone, so a pronoun with no antecedent is a
  fact nobody can retrieve.
- ONE FACT PER SENTENCE. Split "graduated from Uludağ with a 3.17 GPA in
  Computer Programming" into the degree and the GPA only if they answer
  different questions; do not cram unrelated facts together.
- KEEP EVERY SPECIFIC: dates, numbers, scores, amounts, coordinates, full
  names, institution names. These are the entire point — a fact stripped of its
  number answers nothing.
- DURABLE ONLY. Extract what remains true or remains historically true. Skip
  narration, mood, foreshadowing, and the author's commentary.
- INVENT NOTHING and INFER NOTHING. If the text does not say it, it is not a
  fact.
- Under 300 characters each.

For each fact also give:
  domain  — one of: biography (who he is, durable background), preference (what
            he likes, dislikes or wants), state (true of his life right now),
            project, training, finance, event.
  subject — "owner" for a fact about him; "person:<Name>" for a fact about
            someone else; "project:<Name>" for a fact about a project.

SECTION:
{section}

Return ONLY a JSON array:
[{{"text": "<the fact>", "domain": "biography", "subject": "owner"}}]
"""


def _parse_json_array(raw: str) -> list[dict]:
    body = (raw or "").strip()
    if body.startswith("```"):
        body = re.sub(r"^```[a-z]*\s*", "", body)
        body = re.sub(r"\s*```$", "", body)
    start, end = body.find("["), body.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        return [p for p in json.loads(body[start : end + 1]) if isinstance(p, dict)]
    except json.JSONDecodeError:
        return []


def split_sections(text: str) -> list[str]:
    """Narrative → sections, each small enough to extract from in one call."""
    parts = [p.strip() for p in SECTION.split(text) if p.strip()]
    out: list[str] = []
    for part in parts:
        while len(part) > MAX_SECTION_CHARS:
            cut = part.rfind("\n\n", 0, MAX_SECTION_CHARS)
            cut = cut if cut > 0 else MAX_SECTION_CHARS
            out.append(part[:cut])
            part = part[cut:].lstrip()
        if part:
            out.append(part)
    return out


async def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--user-id", type=int, default=1)
    p.add_argument("--path", action="append", default=None, help="Narrative file to distil (repeatable).")
    p.add_argument("--model", default="")
    p.add_argument("--dup-threshold", type=float, default=0.93,
                   help="Cosine similarity against an existing fact above which a candidate is a duplicate.")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--commit", action="store_true")
    args = p.parse_args()

    import numpy as np
    from sqlalchemy import select

    from app.config import settings
    from app.database import AsyncSessionLocal
    from app.models.memory_file import MemoryFile
    from app.models.observation import Observation
    from app.services import lexical
    from app.services.embeddings import embed_texts
    from app.services.llm_client import LLMClient
    from app.services.observations import (
        ObservationRejected,
        _live,
        validate_observation,
    )

    paths = args.path or DEFAULT_PATHS
    model = args.model or settings.llm_background_model or settings.llm_main_model
    client = LLMClient()

    async with AsyncSessionLocal() as db:
        files = (
            await db.execute(
                select(MemoryFile).where(
                    MemoryFile.user_id == args.user_id, MemoryFile.path.in_(paths)
                )
            )
        ).scalars().all()
        existing = list((await db.execute(_live(args.user_id))).scalars().all())

    if not files:
        print(f"\n  None of {paths} exist for user {args.user_id}.\n")
        return

    known_text = {" ".join(o.content.lower().split()) for o in existing}
    known_vectors = [o for o in existing if o.embedding]
    known_matrix = (
        np.stack([np.frombuffer(o.embedding, dtype=np.float32) for o in known_vectors])
        if known_vectors else None
    )
    print(f"\n  {len(existing)} live observations already recorded ({len(known_vectors)} embedded)")

    # ── Extract ──────────────────────────────────────────────────────────────
    candidates: list[dict] = []
    for f in files:
        sections = split_sections(f.content)
        print(f"  {f.path}: {len(f.content)} chars → {len(sections)} section(s)")
        for i, section in enumerate(sections, start=1):
            if len(section) < MIN_SECTION_CHARS:
                print(f"    · section {i}/{len(sections)} skipped ({len(section)} chars — too little to extract from)")
                continue
            print(f"    … section {i}/{len(sections)}", flush=True)
            try:
                resp = await client.create_message(
                    model=model,
                    system="You extract atomic facts. You return only the JSON array asked for.",
                    messages=[{"role": "user", "content": _PROMPT.format(section=section)}],
                    max_tokens=8192,
                    # Reasoning models otherwise spend the entire budget thinking and
                    # return an empty message — the same trap app/services/memory.py
                    # documents for title generation. This is extraction, not
                    # reasoning; it needs the tokens for output.
                    reasoning_effort="minimal",
                )
            except Exception as e:  # noqa: BLE001
                print(f"    ! section {i} failed: {e}")
                continue
            raw = resp.content[0].text if resp.content else ""
            for item in _parse_json_array(raw):
                text = str(item.get("text") or "").strip()
                if text:
                    candidates.append({
                        "text": text,
                        "domain": str(item.get("domain") or "biography").strip().lower(),
                        "subject": str(item.get("subject") or "owner").strip(),
                        "source": f.path,
                        "_section": section,
                    })

    print(f"\n  {len(candidates)} candidate fact(s) extracted")
    if not candidates:
        return

    # ── Validate (the same gate the live write path uses) ────────────────────
    valid, rejected, ungrounded = [], [], []
    for c in candidates:
        missing = ungrounded_tokens(c["text"], c["_section"])
        if missing:
            ungrounded.append((c, missing))
            continue
        try:
            validate_observation(
                content=c["text"], level="explicit",
                subject=c["subject"], domain=c["domain"],
            )
            valid.append(c)
        except ObservationRejected as e:
            rejected.append((c, str(e)))
        except Exception as e:  # noqa: BLE001
            rejected.append((c, str(e)))

    # ── Deduplicate ──────────────────────────────────────────────────────────
    #ical duplicates first (free), then semantic ones against the store AND
    # against candidates already accepted in this run — a narrative repeats
    # itself across sections, so the run duplicates itself if it only checks the
    # store it started from.
    unique, dup_exact, dup_similar = [], [], []
    accepted_vectors: list = []
    for c in valid:
        if " ".join(c["text"].lower().split()) in known_text:
            dup_exact.append(c)
        else:
            unique.append(c)

    if unique:
        vectors = []
        for i in range(0, len(unique), 64):
            vectors.extend(await embed_texts([c["text"] for c in unique[i : i + 64]]))
        kept = []
        for c, vec in zip(unique, vectors):
            best, against = 0.0, ""
            if known_matrix is not None:
                sims = known_matrix @ vec
                idx = int(np.argmax(sims))
                best, against = float(sims[idx]), known_vectors[idx].content
            for prev_vec, prev_text in accepted_vectors:
                sim = float(prev_vec @ vec)
                if sim > best:
                    best, against = sim, prev_text
            if best >= args.dup_threshold:
                dup_similar.append((c, best, against))
            else:
                c["_vector"] = vec
                accepted_vectors.append((vec, c["text"]))
                kept.append(c)
        unique = kept

    print(f"  {len(ungrounded)} REJECTED AS UNGROUNDED (a token the source never mentions)")
    print(f"  {len(rejected)} rejected by the guard")
    print(f"  {len(dup_exact)} exact duplicate(s) of existing facts")
    print(f"  {len(dup_similar)} near-duplicate(s) above {args.dup_threshold}")
    print(f"  {len(unique)} NEW fact(s) to record\n")

    for c in unique:
        print(f"  + [{c['domain']} · {c['subject']}] {c['text'][:150]}")
    for c, missing in ungrounded[:25]:
        print(f"  ⚠ ungrounded {missing}: {c['text'][:110]}")
    for c, reason in rejected[:20]:
        print(f"  ✗ {c['text'][:90]} — {reason[:90]}")
    for c, sim, against in dup_similar[:20]:
        print(f"  = {c['text'][:80]}\n      ~{sim:.2f} {against[:80]}")

    if args.dry_run:
        print("\n  Dry run — nothing written.\n")
        return
    if not unique:
        print("  Nothing new to record.\n")
        return

    async with AsyncSessionLocal() as db:
        now = datetime.now(timezone.utc)
        rows = []
        for c in unique:
            obs = Observation(
                user_id=args.user_id,
                observer="owner",
                origin="seed",
                content=c["text"],
                level="explicit",
                subject=c["subject"],
                domain=c["domain"],
                embedding=c["_vector"].tobytes(),
                created_at=now,
                updated_at=now,
            )
            db.add(obs)
            rows.append(obs)
        await db.flush()
        for obs in rows:
            await lexical.index_observation(db, obs)
        await db.commit()

    print(f"\n  Recorded {len(rows)} new observation(s) from {', '.join(paths)}.\n")


if __name__ == "__main__":
    asyncio.run(main())
