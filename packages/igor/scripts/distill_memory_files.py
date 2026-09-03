# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Distil the narrative memory files into retrievable facts.

## Why

/memories holds 190,112 characters of prose across 104 files — one per person,
per project, per academic and financial thread. Recall cannot reach any of it.
`search_memory` searches observations; these are markdown, indexed by nothing.
A handful are preloaded into the system prompt, and the rest are reachable only
if an agent already knows the path and thinks to open it.

That is the whole shape of "I have to explain the most simple things now":

  * A fact written once into `social/professional/sinan-kara.md` is not findable
    by asking about Sinan Kara. It is findable by an agent that already
    suspected the file existed.
  * Injection does not scale as attention. owner.md alone is 13.8 KB inside a
    28.7 KB memory block. A fact stated once in the middle of that competes with
    everything else in the prompt, and it stopped winning as the block grew.
  * The store meanwhile grew in the wrong places — 248 project and 179 training
    observations against 57 biography — so recall got louder without getting
    more knowledgeable.

The narrative is worth keeping; it is good context and it reads well. The fix is
that every ATOMIC fact inside it ALSO exists as an observation, so it can be
retrieved by a question instead of only found by a reader who already knew where
to look.

## What it does

  1. Walks the narrative files (every non-audit, non-archive file by default),
     splits each into sections, and extracts atomic facts.
  2. Infers the SUBJECT from the path, which is free and reliable:
     `social/professional/sinan-kara.md` is about `person:Sinan Kara`,
     `projects/siberay.md` about `project:Siberay`. The entity's real name comes
     from the file's own H1 where it has one, so "Doç. Dr. Hakan Eren" is not
     flattened into "Doc Dr Hakan Eren".
  3. Extracts TEMPORAL VALIDITY alongside each fact. This is what makes "where
     does he work right now" answerable: without an end date on the fifteen jobs
     he has left, the current one is one row among sixteen equally-live ones and
     recall has no basis to prefer it. Only 8 of 780 rows carried a `valid_until`
     before this ran.
  4. Validates every candidate through the same `validate_observation` the live
     write path uses, including the fragment guard.
  5. GROUNDS every candidate: each number and proper noun in an extracted fact
     must appear in the section it came from. This is not optional. On the first
     run a starved section of owner.md produced an entire invented biography — a
     birthplace in Eskişehir, a father with a textile shop, a high school, a
     barista job. Fabricated memory is far worse than missing memory.
  6. Deduplicates on exact text and on cosine similarity, against both the
     existing store and the facts accepted earlier in the same run.

Nothing in the source files is modified or deleted. This is purely additive and
safe to re-run: a second pass adds only what the first did not.

## Running it

    docker cp scripts/distill_memory_files.py speda-app-1:/tmp/
    docker exec -w /app/packages/igor speda-app-1 \
        /app/.venv/bin/python /tmp/distill_memory_files.py --user-id 1 --all --dry-run

    ... --all --commit

`--path` distils specific files instead (repeatable). `--all` walks everything
under /memories except `.audit/` and `.archive/`, which are machine logs and
demoted history rather than current knowledge.
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

# Paths that are history or machine output rather than current knowledge. The
# archive is DEMOTED facts — distilling it would resurrect exactly what someone
# decided was no longer true — and the audit tree is Orion's own bookkeeping.
EXCLUDED_PREFIXES = ("/memories/.audit/", "/memories/.archive/")

# Files whose content is a RENDERING of the observation store rather than a
# source for it. Distilling these would feed the store its own output back,
# which manufactures reinforcement out of nothing and makes a fact look
# better-established every time the renderer runs.
EXCLUDED_PATHS = frozenset({
    "/memories/current.md",     # rendered from live observations
    "/memories/history.md",     # rendered from ended observations
    "/memories/log.md",         # rolling session log, rewritten daily
})

_H1 = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


def entity_for(path: str, content: str) -> tuple[str, str]:
    """(subject, human name) for a narrative file, inferred from its path.

    The directory says WHAT KIND of thing the file is about and the H1 says what
    it is CALLED. Taking the name from the heading rather than the slug is what
    keeps "Doç. Dr. Hakan Eren" from being stored as "Doc Dr Hakan Eren" and
    "F.O.R.G.E" from becoming "F O R G E" — the slug is lossy by construction,
    and a subject that does not match how he writes the name is a subject that
    never gets matched.
    """
    stem = path.rsplit("/", 1)[-1].removesuffix(".md")
    heading = _H1.search(content or "")
    # A long H1 is a document title, not an entity name; only a short heading is
    # trustworthy as the thing's actual name.
    name = heading.group(1).strip() if heading else ""
    if not name or len(name) > 60:
        name = stem.replace("-", " ").title()
    # Headings often carry a trailing gloss: "Siberay — cybersecurity club".
    name = re.split(r"\s+[—–-]\s+", name)[0].strip() or stem

    if path.startswith("/memories/social/"):
        return f"person:{name}", name
    if path.startswith("/memories/projects/"):
        return f"project:{name}", name
    return "owner", "Ahmet Erol Bayrak"



_PROMPT = """Below is one section of a memory file about {entity}. Extract every ATOMIC,
DURABLE fact it states.

An atomic fact is ONE self-contained English sentence that answers a question on
its own, read years later with none of this text around it. Rules:

- NAME THE SUBJECT. Write "{entity} was born on 18 October 2004", never "Born in
  2004" and never "He was born". The sentence is stored and retrieved ALONE, so
  a pronoun with no antecedent is a fact nobody can use.
- ONE FACT PER SENTENCE. Do not cram unrelated facts together.
- KEEP EVERY SPECIFIC: dates, numbers, scores, amounts, coordinates, full names,
  institution names. A fact stripped of its number answers nothing.
- DURABLE ONLY. Skip narration, mood, foreshadowing and commentary.
- INVENT NOTHING and INFER NOTHING. Every name and number you write must appear
  in the text below. If it is not there, leave it out.
- ENGLISH ONLY. Translate Turkish source text, but keep proper nouns, place
  names, institution names and currency codes exactly as written.
- Under 300 characters each.

For each fact also give:

  domain  — one of: biography (who someone is, durable background), preference
            (what he likes, dislikes or wants), state (true of his life now),
            project, training, finance, event.

  valid_from  — "YYYY-MM-DD" if the text says when this STARTED being true.
                Omit if it has simply always held or the text does not say.

  valid_until — "YYYY-MM-DD" if the text says when this STOPPED being true.
                THIS MATTERS MORE THAN IT LOOKS. A job he left, a place he used
                to live, a figure that has since changed: without an end date it
                stays indistinguishable from what is true today, and a question
                about the present gets answered with the past. If the text gives
                only a month or a year for the ending, use the last day you can
                justify from it ("August 2026" → 2026-08-31). Omit ONLY for
                things still true, which for a past-tense narrative is the
                minority.

SECTION:
{section}

Return ONLY a JSON array:
[{{"text": "<the fact>", "domain": "biography", "valid_from": "", "valid_until": ""}}]
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


def _parse_day(value: str) -> "object | None":
    """'YYYY-MM-DD' → date, or None for anything else.

    Deliberately strict. A half-parsed date on a validity field is worse than no
    date at all: it decides whether a fact reads as current, and a wrong end date
    silently retires something still true.
    """
    from datetime import date

    text = (value or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


async def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--user-id", type=int, default=1)
    p.add_argument("--path", action="append", default=None, help="Narrative file to distil (repeatable).")
    p.add_argument("--all", action="store_true",
                   help="Distil every narrative file except .audit/, .archive/ and rendered surfaces.")
    p.add_argument("--model", default="")
    p.add_argument("--dup-threshold", type=float, default=0.93,
                   help="Cosine similarity against an existing fact above which a candidate is a duplicate.")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--commit", action="store_true")
    args = p.parse_args()

    if not args.all and not args.path:
        print("\n  Give --all or at least one --path.\n")
        return

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
        normalize_subject,
        validate_observation,
    )

    model = args.model or settings.llm_background_model or settings.llm_main_model
    client = LLMClient()

    async with AsyncSessionLocal() as db:
        stmt = select(MemoryFile).where(MemoryFile.user_id == args.user_id)
        if args.path:
            stmt = stmt.where(MemoryFile.path.in_(args.path))
        files = list((await db.execute(stmt.order_by(MemoryFile.path))).scalars().all())
        existing = list((await db.execute(_live(args.user_id))).scalars().all())

    if args.all:
        files = [
            f for f in files
            if not f.path.startswith(EXCLUDED_PREFIXES)
            and f.path not in EXCLUDED_PATHS
        ]

    if not files:
        print("\n  No narrative files matched.\n")
        return

    known_text = {" ".join(o.content.lower().split()) for o in existing}
    known_vectors = [o for o in existing if o.embedding]
    known_matrix = (
        np.stack([np.frombuffer(o.embedding, dtype=np.float32) for o in known_vectors])
        if known_vectors else None
    )
    total_chars = sum(len(f.content or "") for f in files)
    print(f"\n  {len(existing)} live observations already recorded ({len(known_vectors)} embedded)")
    print(f"  {len(files)} narrative file(s), {total_chars} chars to distil\n")

    # ── Extract ──────────────────────────────────────────────────────────────
    candidates: list[dict] = []
    for n, f in enumerate(files, start=1):
        subject, entity = entity_for(f.path, f.content)
        sections = [s for s in split_sections(f.content) if len(s) >= MIN_SECTION_CHARS]
        if not sections:
            print(f"  [{n}/{len(files)}] {f.path} — nothing substantial to extract")
            continue
        print(f"  [{n}/{len(files)}] {f.path} → {subject} ({len(sections)} section(s))", flush=True)
        for section in sections:
            try:
                resp = await client.create_message(
                    model=model,
                    system="You extract atomic facts. You return only the JSON array asked for.",
                    messages=[{
                        "role": "user",
                        "content": _PROMPT.format(entity=entity, section=section),
                    }],
                    max_tokens=8192,
                    # Reasoning models otherwise spend the entire budget thinking
                    # and return an empty message — the same trap
                    # app/services/memory.py documents for title generation.
                    reasoning_effort="minimal",
                )
            except Exception as e:  # noqa: BLE001
                print(f"      ! section failed: {e}")
                continue
            raw = resp.content[0].text if resp.content else ""
            for item in _parse_json_array(raw):
                text = str(item.get("text") or "").strip()
                if not text:
                    continue
                candidates.append({
                    "text": text,
                    "domain": str(item.get("domain") or "biography").strip().lower(),
                    "subject": subject,
                    "valid_from": _parse_day(str(item.get("valid_from") or "")),
                    "valid_until": _parse_day(str(item.get("valid_until") or "")),
                    "source": f.path,
                    "_section": section,
                })

    print(f"\n  {len(candidates)} candidate fact(s) extracted")
    if not candidates:
        return

    # ── Ground, then validate ────────────────────────────────────────────────
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
                valid_from=c["valid_from"], valid_until=c["valid_until"],
            )
            valid.append(c)
        except ObservationRejected as e:
            rejected.append((c, str(e)))
        except Exception as e:  # noqa: BLE001
            rejected.append((c, str(e)))

    # ── Deduplicate ──────────────────────────────────────────────────────────
    # Exact duplicates first (free), then semantic ones against the store AND
    # against candidates already accepted in this run — the same fact is written
    # into several files (a project page and the person's page both state who
    # funded it), so a run that only checked the store would duplicate itself.
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

    dated = sum(1 for c in unique if c["valid_until"])
    print(f"  {len(ungrounded)} REJECTED AS UNGROUNDED (a token the source never mentions)")
    print(f"  {len(rejected)} rejected by the guard")
    print(f"  {len(dup_exact)} exact duplicate(s), {len(dup_similar)} near-duplicate(s) above {args.dup_threshold}")
    print(f"  {len(unique)} NEW fact(s) to record — {dated} of them with an end date\n")

    by_subject: dict[str, int] = {}
    for c in unique:
        by_subject[c["subject"]] = by_subject.get(c["subject"], 0) + 1
    for subject, count in sorted(by_subject.items(), key=lambda kv: -kv[1])[:30]:
        print(f"    {count:>4}  {subject}")

    print()
    for c in unique[:250]:
        span = ""
        if c["valid_from"] or c["valid_until"]:
            span = f"  [{c['valid_from'] or '…'} → {c['valid_until'] or 'now'}]"
        print(f"  + [{c['domain']}] {c['text'][:130]}{span}")
    for c, missing in ungrounded[:25]:
        print(f"  ⚠ ungrounded {missing}: {c['text'][:110]}")
    for c, reason in rejected[:15]:
        print(f"  ✗ {c['text'][:90]} — {reason[:90]}")

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
                subject=normalize_subject(c["subject"]),
                domain=c["domain"],
                valid_from=c["valid_from"],
                valid_until=c["valid_until"],
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

    print(f"\n  Recorded {len(rows)} new observation(s) from {len(files)} file(s).\n")


if __name__ == "__main__":
    asyncio.run(main())
