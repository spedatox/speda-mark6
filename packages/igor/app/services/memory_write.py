"""
Shape-aware writes to memory documents (docs/MEMORY_ARCHITECTURE_V4.md §3.2).

`str_replace` edits a document as a flat string. That is how a July expense ends
up under June, how Erasmus notes end up between two lines about HTML template
placeholders, and how a person ends up at the heading level reserved for
categories. The anchor matched; the location was wrong. Nothing could tell,
because nothing knew the document had locations.

These verbs edit the document as a HEADING TREE. The caller names *where* —
a month, a date, a person, a chapter — and this module finds or creates that
place and puts the content in it. Choosing the wrong place stops being possible
by construction, because the caller never supplies a position, only a key.

Every verb still goes out through `memory_schema.check_write`, so ownership, the
verifier and the size caps all apply exactly as they do to a hand write. This
layer decides WHERE; that one decides WHETHER.
"""

import logging
import re
from datetime import date

from app.services.memory_spec import DocumentSpec, spec_for

logger = logging.getLogger(__name__)


class WriteRejected(Exception):
    """The write cannot be placed. Message is written for the model."""


# ── Heading-tree primitives ───────────────────────────────────────────────────

_H = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def _scan(lines: list[str]) -> list[tuple[int, int, str]]:
    """(index, level, title) for every heading, ignoring fenced code."""
    out, fenced = [], False
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        m = _H.match(ln)
        if m:
            out.append((i, len(m.group(1)), m.group(2).strip()))
    return out


def _section_bounds(lines: list[str], idx: int, level: int) -> tuple[int, int]:
    """[start, end) of a section's body — from just after its heading to the next
    heading at the same level or shallower."""
    start = idx + 1
    for i, lvl, _ in _scan(lines):
        if i > idx and lvl <= level:
            return start, i
    return start, len(lines)


def _find(lines: list[str], title: str, level: int,
          within: tuple[int, int] | None = None) -> int | None:
    lo, hi = within or (0, len(lines))
    for i, lvl, t in _scan(lines):
        if lo <= i < hi and lvl == level and t == title:
            return i
    return None


def _find_prefix(lines: list[str], prefix: str, level: int,
                 within: tuple[int, int] | None = None) -> int | None:
    """An index key matches on its prefix — a session heading is
    `### 2026-08-05 — Lift · COMPLETED`, and the key is only the date."""
    lo, hi = within or (0, len(lines))
    for i, lvl, t in _scan(lines):
        if lo <= i < hi and lvl == level and t.startswith(prefix):
            return i
    return None


def _blank(lines: list[str], at: int) -> None:
    if at > 0 and at <= len(lines) and (at == len(lines) or lines[at - 1].strip()):
        lines.insert(at, "")


# ── Index-key placement ───────────────────────────────────────────────────────

def _insert_index_key(
    lines: list[str], key: str, spec: DocumentSpec, scope: tuple[int, int]
) -> int:
    """Create `### key` (or `## key`) in the document's own index order.

    Order matters and is per-document: a finance ledger reads oldest-month-first,
    a session log reads newest-first. Inserting "at the end" would be right for
    one and wrong for the other, so the existing keys decide.
    """
    level = spec.index_level
    lo, hi = scope
    existing = [
        (i, t) for i, lvl, t in _scan(lines)
        if lo <= i < hi and lvl == level and spec.index_pattern
        and re.match(spec.index_pattern, t)
    ]

    heading = "#" * level + " " + key
    if not existing:
        at = hi
        while at > lo and not lines[at - 1].strip():
            at -= 1
        lines.insert(at, "")
        lines.insert(at + 1, heading)
        return at + 1

    keys = [re.match(spec.index_pattern, t).group(0) for _, t in existing]
    descending = len(keys) > 1 and keys[0] > keys[-1]

    for (i, _), k in zip(existing, keys):
        if (key > k) if descending else (key < k):
            lines.insert(i, heading)
            lines.insert(i + 1, "")
            return i

    # Past every existing key: append after the last one's body.
    last_i = existing[-1][0]
    _, end = _section_bounds(lines, last_i, level)
    while end > last_i and not lines[end - 1].strip():
        end -= 1
    lines.insert(end, "")
    lines.insert(end + 1, heading)
    return end + 1


# ── ledger_append ─────────────────────────────────────────────────────────────

def ledger_append(
    text: str, *, path: str, key: str, section: str | None = None,
    row: list[str] | None = None, lines_in: list[str] | None = None,
) -> str:
    """
    Add an entry to a ledger under `key`, creating the key if it is new.

    The caller says which month or which date. It cannot say which byte offset,
    which is the whole point: a row dated 2026-08 cannot land under `## 2026-07`
    because the placement is derived from the key, not from a matched anchor.
    """
    spec = spec_for(path)
    if spec is None or spec.kind != "ledger":
        raise WriteRejected(f"`{path}` is not a ledger; ledger_append does not apply to it.")
    if spec.index_pattern and not re.match(spec.index_pattern, key):
        raise WriteRejected(
            f"`{key}` is not a valid index key for {path.split('/')[-1]} — it must "
            f"match {spec.index_pattern}. A finance month is `2026-08`; a session "
            f"date is `2026-08-09`."
        )

    lines = text.splitlines()

    # Scope: where this document's index lives. wellness.md's sessions are under
    # `## 5. LOG (Chronological)`, not at the top of the file.
    scope = (0, len(lines))
    if spec.index_parent:
        pidx = _find(lines, spec.index_parent, 2)
        if pidx is None:
            raise WriteRejected(
                f"{path.split('/')[-1]} has no `## {spec.index_parent}` section to "
                f"file this under. Restore it before adding entries."
            )
        scope = _section_bounds(lines, pidx, 2)

    kidx = _find_prefix(lines, key, spec.index_level, scope)
    if kidx is None:
        kidx = _insert_index_key(lines, key, spec, scope)
        if spec.entry_style == "bullets" and lines_in:
            # A session heading carries its own descriptor; keep whatever the
            # caller put on the first line.
            pass

    kstart, kend = _section_bounds(lines, kidx, spec.index_level)

    if spec.entry_style == "table":
        if not section:
            raise WriteRejected(
                f"{path.split('/')[-1]} keeps rows under a sub-section. Pass one of: "
                f"{', '.join(spec.entry_sections) or 'see the document'}."
            )
        if spec.entry_sections and section not in spec.entry_sections:
            raise WriteRejected(
                f"`{section}` is not a section of this ledger. Use one of: "
                f"{', '.join(spec.entry_sections)}."
            )
        cols = spec.columns.get(section, ())
        if not row:
            raise WriteRejected("A table ledger needs a `row`.")
        if cols and len(row) != len(cols):
            raise WriteRejected(
                f"`{section}` has {len(cols)} columns ({', '.join(cols)}); "
                f"you supplied {len(row)} value(s). A short row silently shifts "
                f"every cell after it."
            )

        sidx = _find(lines, section, spec.index_level + 1, (kstart, kend))
        if sidx is None:
            at = kend
            while at > kstart and not lines[at - 1].strip():
                at -= 1
            block = ["", "#" * (spec.index_level + 1) + " " + section, ""]
            if cols:
                block += ["| " + " | ".join(cols) + " |",
                          "|" + "|".join(["---"] * len(cols)) + "|"]
            lines[at:at] = block
            sidx = at + 1

        sstart, send = _section_bounds(lines, sidx, spec.index_level + 1)
        at = send
        while at > sstart and not lines[at - 1].strip():
            at -= 1
        lines.insert(at, "| " + " | ".join(str(c) for c in row) + " |")

    else:
        if not lines_in:
            raise WriteRejected("A bullet ledger needs `lines` — the entry's bullets.")
        at = kend
        while at > kstart and not lines[at - 1].strip():
            at -= 1
        body = [("- " + b) if not b.lstrip().startswith(("-", "*")) else b
                for b in lines_in]
        lines[at:at] = body

    return "\n".join(lines).rstrip() + "\n"


# ── registry_upsert ───────────────────────────────────────────────────────────

def registry_upsert(
    text: str, *, path: str, entity: str, category: str | None = None,
    who: str | None = None, event: str | None = None, when: str | None = None,
) -> str:
    """
    Create or update one entity in a registry.

    `who` replaces the entity's description; `event` prepends a dated bullet to
    its event log. A new entity is created with the full shape, so a person can
    never exist here without a `**Who:**` block — the schema is the writer, not a
    convention the writer is asked to remember.
    """
    spec = spec_for(path)
    if spec is None or spec.kind != "registry":
        raise WriteRejected(f"`{path}` is not a registry; registry_upsert does not apply.")
    if not entity.strip():
        raise WriteRejected("`entity` is required — who or what is this about?")

    # One entity per file: the entity is the H1, there is no category heading to
    # find and no sibling to accidentally write into. Most of the machinery below
    # exists to locate an entity inside a 27 KB document and to keep it at the
    # right heading level among its peers — questions that stop existing once the
    # file IS the entity.
    from app.services.memory_spec import collection_for

    coll = collection_for(path)
    if coll is not None:
        return _member_upsert(
            text, coll=coll, entity=entity.strip(), who=who, event=event, when=when,
        )

    level = spec.entity_level or 2
    lines = text.splitlines()
    scope = (0, len(lines))

    if level == 3:
        if not category:
            raise WriteRejected(
                f"{path.split('/')[-1]} groups entities by category. Pass one of: "
                f"{', '.join(spec.sections)}."
            )
        if spec.sections and category not in spec.sections:
            raise WriteRejected(
                f"`{category}` is not a category in this document. Use one of: "
                f"{', '.join(spec.sections)}. Categories are `##`; entities are `###`."
            )
        cidx = _find(lines, category, 2)
        if cidx is None:
            at = len(lines)
            lines[at:at] = ["", "## " + category, ""]
            cidx = at + 1
        scope = _section_bounds(lines, cidx, 2)

    eidx = _find(lines, entity, level, scope)
    if eidx is None:
        at = scope[1]
        while at > scope[0] and not lines[at - 1].strip():
            at -= 1
        block = ["", "#" * level + " " + entity, "",
                 "**Who:** " + (who or "_(not yet established)_"), "",
                 "**Events:**"]
        lines[at:at] = block
        eidx = at + 1
        who = None   # already written into the new block

    estart, eend = _section_bounds(lines, eidx, level)

    if who:
        for i in range(estart, eend):
            if lines[i].startswith("**Who:**"):
                lines[i] = "**Who:** " + who
                break
        else:
            lines.insert(estart, "**Who:** " + who)
            eend += 1

    if event:
        stamp = when or date.today().isoformat()
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", stamp):
            raise WriteRejected(
                f"`when` must be an absolute date as YYYY-MM-DD — got {stamp!r}."
            )
        bullet = f"- [{stamp}] {event}"
        for i in range(estart, eend):
            if lines[i].startswith("**Events:**"):
                lines.insert(i + 1, bullet)   # newest first
                break
        else:
            lines.insert(eend, "")
            lines.insert(eend + 1, "**Events:**")
            lines.insert(eend + 2, bullet)

    return "\n".join(lines).rstrip() + "\n"


def _member_upsert(
    text: str, *, coll, entity: str, who: str | None,
    event: str | None, when: str | None,
) -> str:
    """Upsert inside a collection member, where the entity is the document.

    A collection whose members carry declared markers (people: `**Who:**` /
    `**Events:**`) gets that full shape on creation, for the same reason the
    monolith version did — the schema is the writer, not something the writer is
    asked to remember. A collection with no declared markers (projects, whose
    real section names are still being read off the migration rather than
    guessed) gets its title and its content, and nothing invented around it.
    """
    stamp = when or date.today().isoformat()
    if event and not re.match(r"^\d{4}-\d{2}-\d{2}$", stamp):
        raise WriteRejected(
            f"`when` must be an absolute date as YYYY-MM-DD — got {stamp!r}."
        )

    has_markers = bool(coll.markers)
    lines = text.splitlines() if text.strip() else []

    if not lines:
        frame = [f"# {entity}", ""]
        if has_markers:
            frame += ["**Who:** " + (who or "_(not yet established)_"), "", "**Events:**"]
            who = None
        elif who:
            frame += [who, ""]
            who = None
        lines = frame

    if who:
        if has_markers:
            for i, line in enumerate(lines):
                if line.startswith("**Who:**"):
                    lines[i] = "**Who:** " + who
                    break
            else:
                at = 1 if lines and lines[0].startswith("# ") else 0
                lines[at:at] = ["", "**Who:** " + who]
        else:
            at = 1 if lines and lines[0].startswith("# ") else 0
            lines[at:at] = ["", who]

    if event:
        bullet = f"- [{stamp}] {event}"
        for i, line in enumerate(lines):
            if line.startswith("**Events:**"):
                lines.insert(i + 1, bullet)      # newest first
                break
        else:
            while lines and not lines[-1].strip():
                lines.pop()
            lines += ["", "**Events:**" if has_markers else "## Log", bullet]

    return "\n".join(lines).rstrip() + "\n"


# ── narrative_revise ──────────────────────────────────────────────────────────

def narrative_revise(text: str, *, path: str, chapter: str, body: str) -> str:
    """
    Replace one chapter of a narrative document, and only that chapter.

    Scoped on purpose: the biography is fifteen kilobytes the owner and eight
    agents built over months, and the last thing that rewrote a memory document
    wholesale destroyed a financial ledger. One chapter at a time is the widest
    licence this verb grants.
    """
    spec = spec_for(path)
    if spec is None or spec.kind != "narrative":
        raise WriteRejected(f"`{path}` is not a narrative document.")

    lines = text.splitlines()
    idx = _find(lines, chapter, 2)
    if idx is None:
        raise WriteRejected(
            f"`{chapter}` is not a chapter of {path.split('/')[-1]}. Existing chapters: "
            + ", ".join(t for _, lvl, t in _scan(lines) if lvl == 2)
        )
    start, end = _section_bounds(lines, idx, 2)
    new = [""] + body.strip().splitlines() + [""]
    lines[start:end] = new
    return "\n".join(lines).rstrip() + "\n"
