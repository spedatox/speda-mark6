"""
The verifier — the mechanism that keeps memory correct
(docs/MEMORY_ARCHITECTURE_V4.md §3.3).

Every check here exists because it caught something real when the store was read
end to end. None of them is speculative:

  corrupted_text      a leaked tool-call preamble, mojibake across four scripts
                      and Chinese gambling spam, written into academic.md on
                      2026-07-20 and still there three weeks later
  content_above_title a paragraph sitting above the H1 in current.md, from an
                      `insert` at line 0
  single_title        a second H1 halfway down academic.md — four documents in
                      one file
  known_sections      Erasmus notes filed under `## HTML Template`, between two
                      lines about template placeholders
  heading_levels      `## Semra Bayrak` among peers at `###` in social.md, the
                      exact ambiguity that made a migration read a CATEGORY as a
                      person
  required_sections   dossier.md's `## Explicit prohibitions` — hard behavioural
                      rules — vanishing into a heading called "General"
  stale_references    ops.md routing an agent to `packages/api/` (renamed months
                      ago) and asserting "no Postgres" while Postgres runs

Two rules make it safe to switch on against a live store, and they are the same
two that make the schema gate safe:

1. **Only newly-introduced violations block.** A write is checked as a delta.
   Pre-existing mess never blocks an unrelated edit, so enforcement can land
   before any cleanup has happened.
2. **Findings are repaired, never regenerated.** A violation is fixed by editing
   that violation. Nothing here rewrites a document — that is what v3 did, and
   it cost the finance ledger its structure.
"""

import logging
import re
import unicodedata
from dataclasses import dataclass

from app.services.memory_spec import (
    LEDGER,
    NARRATIVE,
    OBSERVATIONS,
    REGISTRY,
    SYSTEM,
    DocumentSpec,
    is_retired,
    spec_for,
)

logger = logging.getLogger(__name__)

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


@dataclass
class Finding:
    path: str
    rule: str
    severity: str          # error | warning | info
    message: str
    line: int | None = None
    fix: str = ""

    def as_dict(self) -> dict:
        return {
            "path": self.path, "rule": self.rule, "severity": self.severity,
            "message": self.message, "line": self.line, "fix": self.fix,
        }


# ── Corruption detection ──────────────────────────────────────────────────────

# Fragments of the model's own wire protocol. If one of these reaches a memory
# file, a generation broke mid-flight and was persisted as knowledge.
_TOOL_FRAGMENTS = re.compile(
    r"(assistant\s+to=|<\|[a-z_]+\|>|functions\.[a-z_]+\s*\{|"
    r"^\s*\}\s*$(?=\n\s*(?:###|\w))|json error\?|Need retry)",
    re.IGNORECASE | re.MULTILINE,
)

# Control characters that no human types and no renderer needs.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Codepoint ranges that cannot legitimately appear in this store. An allowlist by
# script name is the obvious approach and it is wrong: it flags box-drawing
# characters (`├── └──`, used in every directory tree in projects.md), arrows,
# check marks and typographic dashes — all of which the owner writes constantly.
# A denylist of scripts he demonstrably does not use is both narrower and
# accurate. Turkish, Greek, Cyrillic, Arabic and every symbol block pass through.
_FOREIGN_RANGES = (
    (0x0900, 0x0DFF),    # Devanagari … Sinhala (the academic.md corruption)
    (0x3000, 0x30FF),    # CJK punctuation, Hiragana, Katakana
    (0x3400, 0x4DBF),    # CJK Ext A
    (0x4E00, 0x9FFF),    # CJK Unified (the gambling spam)
    (0xAC00, 0xD7AF),    # Hangul
    (0xE000, 0xF8FF),    # Private use
    (0xF0000, 0x10FFFF),  # Supplementary private use / noise
)


def _is_foreign(ch: str) -> bool:
    cp = ord(ch)
    if any(lo <= cp <= hi for lo, hi in _FOREIGN_RANGES):
        return True
    # Unassigned codepoints are decoder wreckage by definition.
    return unicodedata.category(ch) in ("Cn", "Co", "Cs")


def _mojibake_runs(text: str, min_run: int = 2) -> list[tuple[int, str]]:
    """Runs of characters from scripts this store never legitimately uses.

    Run-based on purpose: one stray glyph is a typo, several together is a
    decoder failure. The threshold is low because the real corruption came in
    long runs and a two-character CJK fragment is already inexplicable here.
    """
    hits, run, start = [], [], 0
    for i, ch in enumerate(text):
        if _is_foreign(ch):
            if not run:
                start = i
            run.append(ch)
        else:
            if len(run) >= min_run:
                hits.append((start, "".join(run)))
            run = []
    if len(run) >= min_run:
        hits.append((start, "".join(run)))
    return hits


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


# ── Individual checks ─────────────────────────────────────────────────────────

def _headings(text: str) -> list[tuple[int, int, str]]:
    """(line_number, level, title) for every ATX heading outside code fences."""
    out, fenced = [], False
    for n, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        m = re.match(r"^(#{1,6})\s+(.*\S)\s*$", line)
        if m:
            out.append((n, len(m.group(1)), m.group(2).strip()))
    return out


def check_corrupted_text(path: str, text: str) -> list[Finding]:
    findings = []
    for m in _TOOL_FRAGMENTS.finditer(text):
        findings.append(Finding(
            path, "corrupted_text", "error",
            f"Fragment of the model's wire protocol in the document: {m.group(0)[:60]!r}. "
            f"A generation broke mid-flight and was saved as knowledge.",
            line=_line_of(text, m.start()),
            fix="Delete the line. Nothing in it was ever a fact.",
        ))
    for offset, run in _mojibake_runs(text):
        findings.append(Finding(
            path, "corrupted_text", "error",
            f"Undecodable character run {run[:24]!r} — scripts this store never uses. "
            f"A decoder failed and the failure was persisted.",
            line=_line_of(text, offset),
            fix="Delete the run, or restore the line from its revision.",
        ))
    if _CONTROL.search(text):
        findings.append(Finding(
            path, "corrupted_text", "error",
            "Control characters in the document.",
            line=_line_of(text, _CONTROL.search(text).start()),
            fix="Strip them.",
        ))
    return findings


def check_document_frame(path: str, text: str, spec: DocumentSpec) -> list[Finding]:
    """One H1, nothing above it, no second H1."""
    findings = []
    heads = _headings(text)
    h1s = [(n, t) for n, lvl, t in heads if lvl == 1]

    if not h1s:
        findings.append(Finding(
            path, "single_title", "warning",
            "No H1 — the document has no title.",
            fix=f"Add `# {spec.summary}` as the first line.",
        ))
    else:
        first_line = h1s[0][0]
        above = [
            (n, ln) for n, ln in enumerate(text.splitlines()[: first_line - 1], 1)
            if ln.strip() and not ln.lstrip().startswith("<!--")
        ]
        if above:
            findings.append(Finding(
                path, "content_above_title", "error",
                f"{len(above)} line(s) of content sit ABOVE the document title — "
                f"almost always an `insert` at line 0.",
                line=above[0][0],
                fix="Move the text into the section it belongs to, below the title.",
            ))
        if len(h1s) > 1:
            findings.append(Finding(
                path, "single_title", "error",
                f"{len(h1s)} H1 headings — this is {len(h1s)} documents in one file "
                f"(second: {h1s[1][1]!r}).",
                line=h1s[1][0],
                fix="Demote it to `##`, or split the file.",
            ))
    return findings


def check_sections(path: str, text: str, spec: DocumentSpec) -> list[Finding]:
    findings = []
    heads = _headings(text)
    top = [(n, t) for n, lvl, t in heads if lvl == 2]
    titles = {t for _, t in top}

    if spec.sections:
        allowed = set(spec.sections)
        if spec.index_pattern and spec.index_level == 2:
            pat = re.compile(spec.index_pattern)
            unknown = [(n, t) for n, t in top if t not in allowed and not pat.match(t)]
        else:
            unknown = [(n, t) for n, t in top if t not in allowed]
        for n, t in unknown:
            findings.append(Finding(
                path, "known_sections", "warning",
                f"Section {t!r} is not in this document's spec. Either it is "
                f"content filed in the wrong place, or the spec has fallen behind.",
                line=n,
                fix="Move the content, or widen the spec in the same commit.",
            ))

    for want in spec.required:
        if want not in titles:
            findings.append(Finding(
                path, "required_sections", "error",
                f"Required section {want!r} is missing.",
                fix=f"Restore `## {want}` — something removed a load-bearing section.",
            ))
    return findings


def check_heading_levels(path: str, text: str, spec: DocumentSpec) -> list[Finding]:
    """Entities in a registry must all sit at the same level."""
    findings = []
    if spec.kind != REGISTRY or spec.entity_level is None:
        return findings

    heads = _headings(text)
    if spec.entity_level == 3:
        # Two-level registry: `## Category` then `### Person`. A person written at
        # `##` becomes a category — which is how a migration turned "Professional"
        # into a person with 21 facts.
        categories = set(spec.sections)
        strays = [
            (n, t) for n, lvl, t in heads
            if lvl == 2 and spec.sections and t not in categories
        ]
        for n, t in strays:
            findings.append(Finding(
                path, "heading_levels", "error",
                f"{t!r} is at `##`, the level this document uses for CATEGORIES, "
                f"but it is not one. Every entity here belongs at `###`.",
                line=n,
                fix=f"Demote to `### {t}` under the right category.",
            ))
    return findings


def check_ledger_index(path: str, text: str, spec: DocumentSpec) -> list[Finding]:
    findings = []
    if spec.kind != LEDGER or not spec.index_pattern:
        return findings
    pat = re.compile(spec.index_pattern)
    keys = [
        (n, t) for n, lvl, t in _headings(text)
        if lvl == spec.index_level and pat.match(t)
    ]
    seen: dict[str, int] = {}
    for n, t in keys:
        key = pat.match(t).group(0)
        if key in seen:
            findings.append(Finding(
                path, "duplicate_index", "warning",
                f"Index key {key!r} appears twice ({seen[key]} and {n}). Entries "
                f"for one period split across two places are entries that get missed.",
                line=n,
                fix="Merge the two sections.",
            ))
        seen[key] = n
    return findings


# References that go stale silently and are then acted on. Each is a real path or
# claim found wrong in ops.md.
_STALE_REFERENCES: tuple[tuple[str, str], str] = (
    (("packages/api/", "renamed to packages/igor/ — an agent following this looks in a directory that does not exist"), "error"),
    (("/app/packages/api/", "renamed to /app/packages/igor/"), "error"),
    (("No Postgres", "the Postgres container is running; the store is not SQLite-only"), "warning"),
    (("Igor runs on SQLite", "verify before relying on it — production runs Postgres"), "warning"),
)


def check_stale_references(path: str, text: str, spec: DocumentSpec) -> list[Finding]:
    """Operational claims that were true once and are acted on as if still true."""
    findings = []
    if spec.kind not in (LEDGER, SYSTEM) or spec.owner_agent != "orion":
        return findings
    for (needle, why), severity in _STALE_REFERENCES:
        idx = text.find(needle)
        if idx != -1:
            findings.append(Finding(
                path, "stale_reference", severity,
                f"{needle!r} — {why}.",
                line=_line_of(text, idx),
                fix="Re-verify against the running system and correct the line.",
            ))
    return findings


def check_member_title(path: str, text: str, spec: DocumentSpec) -> list[Finding]:
    """In a collection, the filename IS the entity's identity — so the H1 has to
    agree with it.

    This is the split's replacement for `heading_levels`. In a single 27 KB
    document the question was "is this heading a person or a category", and
    getting it wrong produced a person called "Professional" with 21 facts
    attached. Here the question is a string comparison against the path, and a
    person renamed in their own document without the file being renamed shows up
    immediately instead of quietly becoming a second person.
    """
    from app.services.memory_spec import collection_for, slugify

    coll = collection_for(path)
    if coll is None:
        return []

    stem = path.rsplit("/", 1)[-1][:-3]
    h1 = next((t for _, lvl, t in _headings(text) if lvl == 1), None)
    if h1 is None:
        return []          # check_document_frame already reports the missing H1

    if slugify(h1) != stem:
        return [Finding(
            path, "member_title", "warning",
            f"The title {h1!r} slugifies to {slugify(h1)!r} but the file is "
            f"{stem!r}. The filename is this {coll.entity_noun}'s identity, so "
            f"the two disagreeing means one of them is about somebody else.",
            line=1,
            fix=f"Rename the file to `{coll.root}/"
                + (f"{path.split('/')[-2]}/" if coll.depth == 2 else "")
                + f"{slugify(h1)}.md`, or correct the title.",
        )]
    return []


def check_superseded(path: str, text: str, spec: DocumentSpec) -> list[Finding]:
    """A document that has been split but not yet removed."""
    if not spec.superseded_by:
        return []
    return [Finding(
        path, "superseded", "info",
        f"This document has been split into one file per entity under "
        f"`{spec.superseded_by}/`. It is read-only for agents and kept only so "
        f"nothing is lost while the migration finishes.",
        fix=f"Run the split, confirm `{spec.superseded_by}/` is complete, then "
            f"retire this file (remove it from CANONICAL_FILES, DEFAULT_FILES "
            f"and SPECS, and delete it from the systems board).",
    )]


def check_size(path: str, text: str, spec: DocumentSpec) -> list[Finding]:
    size = len(text.encode("utf-8"))
    if size <= spec.max_bytes:
        return []
    where = "injected into every turn for every agent" if spec.injected else "read on demand"
    return [Finding(
        path, "size", "warning",
        f"{size / 1024:.1f}K over a {spec.max_bytes / 1024:.0f}K cap ({where}).",
        fix="Compress the oldest entries; the trend is the durable asset, not the raw detail.",
    )]


# ── Entry points ──────────────────────────────────────────────────────────────

_CHECKS = (
    check_corrupted_text,
    check_document_frame,
    check_sections,
    check_heading_levels,
    check_member_title,
    check_superseded,
    check_ledger_index,
    check_stale_references,
    check_size,
)


def verify_document(path: str, text: str) -> list[Finding]:
    """Every finding for one document. Returns [] for an unspec'd path."""
    if is_retired(path):
        return [Finding(
            path, "retired", "warning",
            "This document is retired and should not exist.",
            fix="Remove it with DELETE /memory/files (recoverable from the trail).",
        )]
    spec = spec_for(path)
    if spec is None:
        return [Finding(
            path, "unknown_document", "warning",
            "No spec — nothing can check what belongs in this file.",
            fix="Add a DocumentSpec in app/services/memory_spec.py.",
        )]

    findings: list[Finding] = []
    for check in _CHECKS:
        try:
            findings.extend(
                check(path, text) if check is check_corrupted_text
                else check(path, text, spec)
            )
        except Exception as e:  # noqa: BLE001
            # A broken check must never block a write or hide the other checks.
            logger.error(
                "memory_verify_check_failed",
                extra={"path": path, "check": check.__name__, "error": str(e)},
            )
    findings.sort(key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.line or 0))
    return findings


def introduced_by(path: str, before: str, after: str) -> list[Finding]:
    """
    Findings this write would ADD — the delta rule.

    A document with pre-existing problems must stay editable, or nothing could
    ever be repaired. Only what the write newly breaks is attributable to it.
    """
    if before == after:
        return []
    old = {(f.rule, f.message) for f in verify_document(path, before)} if before else set()
    return [f for f in verify_document(path, after) if (f.rule, f.message) not in old]


async def verify_all(db, user_id: int) -> dict:
    """Report over the whole store. Read-only; repairs nothing."""
    from sqlalchemy import select

    from app.models.memory_file import MemoryFile

    rows = (
        await db.execute(select(MemoryFile).where(MemoryFile.user_id == user_id))
    ).scalars().all()

    findings: list[Finding] = []
    for f in rows:
        if f.path.startswith("/memories/."):
            continue
        findings.extend(verify_document(f.path, f.content or ""))

    counts = {"error": 0, "warning": 0, "info": 0}
    for f in findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1

    logger.info(
        "memory_verify_report",
        extra={"user_id": user_id, "documents": len(rows), **counts},
    )
    return {
        "documents": len(rows),
        "counts": counts,
        "findings": [f.as_dict() for f in findings],
        "verdict": (
            "clean" if counts["error"] == 0 and counts["warning"] == 0
            else f"{counts['error']} error(s), {counts['warning']} warning(s)"
        ),
    }
