"""
Split a monolithic memory document into a directory
(docs/MEMORY_ARCHITECTURE_V4.md §2.2, extended by memory_spec.COLLECTIONS).

Two shapes, one operation:

  - a REGISTRY splits by ENTITY — one person, one project, one file — and the
    member set is open, because a new person is a new file;
  - a domain document splits by TOPIC into the closed, named member set its
    CollectionSpec declares — `wellness/sessions.md`, `finance/ledger.md` — so
    the destination of every section is decided in the spec rather than derived
    from its heading text.

The operation is a CUT, never a regeneration. Every byte of an entity's prose
moves into its own file unchanged; the only edit is heading level, because a
project that was `## Speda Mark VI` inside a shared document becomes `# Speda
Mark VI` at the top of its own. That transformation is mechanical, deterministic
and reversible, which is the whole difference between this and what v3 did to
the finance ledger — v3 asked a model to re-express 71 table rows as sentences
and lost every relationship between them (v4 §2.3). Nothing here reads a word it
writes.

Two properties make this safe to run against the owner's live memory:

  1. **It is purely additive.** The monolith is never modified and never
     deleted. Its preamble — the schema comments and the document intro that
     belong to no single entity — therefore cannot be lost: it stays exactly
     where it is. Retiring the original is a separate, deliberate decision the
     owner makes after reading the split, not a side effect of running it.
  2. **It refuses to guess.** A person under no category, two entities whose
     names slugify to the same file, an entity whose body is empty — each is
     reported and skipped, not resolved by picking something plausible. A
     migration that guesses is how `social.md` acquired a person called
     "Professional" with 21 facts attached.
"""

import logging
import re
from dataclasses import dataclass, field

from app.services.memory_spec import CollectionSpec, member_path, slugify

logger = logging.getLogger(__name__)


@dataclass
class Member:
    """One entity, destined for its own file."""

    path: str
    title: str
    group: str | None
    content: str
    source_line: int
    bytes: int = 0

    def __post_init__(self) -> None:
        self.bytes = len(self.content.encode("utf-8"))


@dataclass
class Problem:
    kind: str            # ungrouped | collision | empty | oversize
    title: str
    line: int
    detail: str


@dataclass
class SplitPlan:
    source: str
    root: str
    members: list[Member] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)
    preamble_lines: int = 0
    sections_seen: dict[str, int] = field(default_factory=dict)

    @property
    def clean(self) -> bool:
        return not self.problems

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "root": self.root,
            "members": [
                {"path": m.path, "title": m.title, "group": m.group,
                 "bytes": m.bytes, "source_line": m.source_line}
                for m in self.members
            ],
            "problems": [
                {"kind": p.kind, "title": p.title, "line": p.line, "detail": p.detail}
                for p in self.problems
            ],
            "preamble_lines": self.preamble_lines,
            # The section-name histogram is the point of the dry run: it is the
            # only honest source for tightening CollectionSpec.sections, which is
            # deliberately left empty until the real document has been read.
            "sections_seen": dict(sorted(
                self.sections_seen.items(), key=lambda kv: (-kv[1], kv[0])
            )),
        }


_ATX = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def _walk(text: str):
    """(line_number, line, level, title) — level 0 for non-headings.

    Fence-aware: `projects.md` is full of directory trees inside code fences and
    every one of their `#` comment lines would otherwise read as a heading and
    cut the document in the wrong place.
    """
    fenced = False
    for n, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            yield n, line, 0, ""
            continue
        m = None if fenced else _ATX.match(line)
        if m:
            yield n, line, len(m.group(1)), m.group(2).strip()
        else:
            yield n, line, 0, ""


def _promote(lines: list[str], lift: int) -> list[str]:
    """Raise every heading by `lift` levels, leaving code fences alone."""
    if lift <= 0:
        return lines
    out, fenced = [], False
    for line in lines:
        if line.lstrip().startswith("```"):
            fenced = not fenced
            out.append(line)
            continue
        m = None if fenced else _ATX.match(line)
        if m:
            level = max(1, len(m.group(1)) - lift)
            out.append("#" * level + " " + m.group(2))
        else:
            out.append(line)
    return out


def plan_split(text: str, coll: CollectionSpec) -> SplitPlan:
    """Work out every file the split would write. Pure — touches no database."""
    if coll.closed:
        return _plan_sections(text, coll)
    return _plan_entities(text, coll)


def _plan_sections(text: str, coll: CollectionSpec) -> SplitPlan:
    """Split a domain document into its declared TOPIC files.

    Same operation as the entity split below and the same guarantee — every byte
    of a section's body moves unchanged — with two differences that come from
    the member set being closed and named:

      1. **The destination is declared, not derived.** A section goes where its
         `MemberSpec` says, so `## 5. LOG (Chronological)` becomes
         `wellness/sessions.md` rather than `wellness/5-log-chronological.md`.
         The H1 is the member's declared title; the numbering was an artifact of
         ordering sections inside one file and means nothing in a folder.
      2. **A section nobody declared is a problem, not a new file.** It is
         reported and left in the monolith. Inventing a member here is how the
         store grows a topic that only the agent who wrote it can find, and it
         is the same class of guess that produced a person called "Professional".
    """
    plan = SplitPlan(source=coll.split_from, root=coll.root)

    # section title → the member taking it, in declared order.
    bodies: dict[str, list[str]] = {m.stem: [] for m in coll.members}
    seen: dict[str, list[str]] = {m.stem: [] for m in coll.members}

    current: str | None = None       # stem currently being filled
    promote: bool = False            # this section is a member on its own
    heading_line: str = ""
    first_section = 0

    def take(title: str) -> object | None:
        import re as _re
        for m in coll.members:
            if title in m.takes:
                return m
            if m.takes_pattern and _re.match(m.takes_pattern, title):
                return m
        return None

    for n, line, level, heading in _walk(text):
        if level and level == coll.entity_level:
            member = take(heading)
            first_section = first_section or n
            if member is None:
                plan.problems.append(Problem(
                    "unmapped", heading, n,
                    f"No member of {coll.root} takes {heading!r}. It stays in "
                    f"{coll.split_from}. Declare it in memory_spec.COLLECTIONS "
                    f"(members are: {', '.join(m.stem for m in coll.members)}) "
                    f"and re-run, or fold it into a section that exists.",
                ))
                current, promote = None, False
                continue
            current = member.stem
            promote = not member.gathered
            seen[current].append(heading)
            plan.sections_seen[heading] = plan.sections_seen.get(heading, 0) + 1
            # A gathered member keeps the source heading — the months ARE the
            # index of finance/ledger.md. A member that is one section drops it,
            # because that heading becomes the file's own title.
            heading_line = "" if promote else line
            if heading_line:
                bodies[current].append(heading_line)
            continue
        if current is not None:
            bodies[current].append(line)
        elif not first_section:
            plan.preamble_lines += 1

    for m in coll.members:
        raw = bodies[m.stem]
        lines = _promote(raw, 1) if not m.gathered else raw
        body = "\n".join(lines).strip()
        if not body:
            plan.problems.append(Problem(
                "empty", m.stem, 0,
                f"Nothing in {coll.split_from} maps to {m.stem} "
                f"({'pattern ' + m.takes_pattern if m.takes_pattern else ', '.join(m.takes)}). "
                f"The file is not written. Either the section is named "
                f"differently in the document than in the spec, or this topic "
                f"does not exist yet.",
            ))
            continue
        member = Member(
            path=f"{coll.root}/{m.stem}.md",
            title=m.title,
            group=None,
            content=f"# {m.title}\n\n{body}\n",
            source_line=0,
        )
        if member.bytes > (m.max_bytes or coll.max_bytes):
            plan.problems.append(Problem(
                "oversize", m.stem, 0,
                f"{member.bytes / 1024:.1f}K exceeds the "
                f"{(m.max_bytes or coll.max_bytes) / 1024:.0f}K cap. It is still "
                f"written — the cap is a growth check, not a reason to drop the "
                f"owner's data — but it wants compressing.",
            ))
        plan.members.append(member)

    return plan


def _plan_entities(text: str, coll: CollectionSpec) -> SplitPlan:
    plan = SplitPlan(source=coll.split_from, root=coll.root)
    lift = coll.entity_level - 1

    group: str | None = None
    title: str | None = None
    start = 0
    body: list[str] = []
    first_entity_line = 0
    stray: list[tuple[int, str]] = []              # lines under the CURRENT category
    stray_all: list[tuple[str | None, list]] = []  # …and under the ones before it

    def flush() -> None:
        nonlocal title, body, start
        if title is None:
            return
        _emit(plan, coll, title, group, body, start, lift)
        title, body, start = None, [], 0

    for n, line, level, heading in _walk(text):
        if level and level == coll.entity_level:
            flush()
            title, start, body = heading, n, []
            first_entity_line = first_entity_line or n
            continue
        # For a two-level registry the category heading both closes the person
        # before it and sets where the next ones belong.
        if coll.depth == 2 and level and level == coll.entity_level - 1:
            flush()
            stray_all.append((group, stray))
            group, stray = heading, []
            continue
        if title is not None:
            body.append(line)
        elif not first_entity_line:
            plan.preamble_lines += 1
        elif line.strip():
            # Body text under a CATEGORY heading that belongs to no person —
            # `## Siberay Board` carries the board roster before its first
            # `### Person`. It is nobody's file, so the split has nowhere to put
            # it, and until now it was silently dropped: the loop only counted
            # lines as preamble before the first entity, and everything after
            # fell through both branches into nothing. Silent loss is the one
            # outcome a migration must never produce, so it is reported.
            stray.append((n, line))

    flush()
    for group_name, lines in _by_group(stray_all + [(group, stray)]):
        if not lines:
            continue
        preview = " ".join(l.strip() for _, l in lines)[:120]
        plan.problems.append(Problem(
            "stray", group_name or "(no category)", lines[0][0],
            f"{len(lines)} line(s) sit under `{group_name}` but belong to no "
            f"{coll.entity_noun}, so no file would carry them and they would be "
            f"lost: “{preview}”. Move them onto an entity, or somewhere that is "
            f"about the group rather than a person, before splitting.",
        ))
    return plan


def _by_group(pairs):
    """(group, lines) pairs, dropping the empties — one problem per category."""
    return [(g, ls) for g, ls in pairs if ls]


def _emit(
    plan: SplitPlan,
    coll: CollectionSpec,
    title: str,
    group: str | None,
    body: list[str],
    line: int,
    lift: int,
) -> None:
    text = "\n".join(_promote(body, lift)).strip()
    if not text:
        plan.problems.append(Problem(
            "empty", title, line,
            "Entity has a heading but no content — nothing to move. Check the "
            "source; a heading with no body is usually a leftover.",
        ))
        return

    if coll.depth == 2 and not group:
        plan.problems.append(Problem(
            "ungrouped", title, line,
            f"Appears before any category heading, so there is no folder to put "
            f"it in. Categories are: {', '.join(coll.groups)}. File it under one "
            f"in the source, then re-run.",
        ))
        return

    try:
        path = member_path(coll, title, group)
    except ValueError as e:
        plan.problems.append(Problem("ungrouped", title, line, str(e)))
        return

    clash = next((m for m in plan.members if m.path == path), None)
    if clash is not None:
        plan.problems.append(Problem(
            "collision", title, line,
            f"{title!r} and {clash.title!r} (line {clash.source_line}) both map to "
            f"{path}. One would overwrite the other, so neither is written. "
            f"Disambiguate a name in the source.",
        ))
        plan.members.remove(clash)
        return

    content = f"# {title}\n\n{text}\n"
    member = Member(path=path, title=title, group=group, content=content, source_line=line)

    if member.bytes > coll.max_bytes:
        plan.problems.append(Problem(
            "oversize", title, line,
            f"{member.bytes / 1024:.1f}K exceeds the {coll.max_bytes / 1024:.0f}K "
            f"per-{coll.entity_noun} cap. It is still written — the cap is a "
            f"growth check, not a reason to drop the owner's data — but it wants "
            f"compressing.",
        ))

    for _, _, level, heading in _walk(content):
        if level == 2:
            plan.sections_seen[heading] = plan.sections_seen.get(heading, 0) + 1

    plan.members.append(member)


async def apply_split(
    db,
    user_id: int,
    coll: CollectionSpec,
    *,
    dry_run: bool = True,
    request_id: str = "",
) -> dict:
    """
    Plan the split for one collection and, unless `dry_run`, write the members.

    Writes go through `commit_file`, so every created file lands in the revision
    trail and the whole operation can be walked back file by file. The source
    document is not touched: reverting is deleting what this added.
    """
    from sqlalchemy import select

    from app.models.memory_file import MemoryFile
    from app.services.memory_store import commit_file

    row = (
        await db.execute(
            select(MemoryFile).where(
                MemoryFile.user_id == user_id,
                MemoryFile.path == coll.split_from,
            )
        )
    ).scalar_one_or_none()

    if row is None:
        return {
            "source": coll.split_from, "root": coll.root, "status": "absent",
            "verdict": f"{coll.split_from} does not exist — nothing to split.",
        }

    plan = plan_split(row.content or "", coll)
    report = plan.as_dict()
    report["dry_run"] = dry_run
    report["source_bytes"] = len((row.content or "").encode("utf-8"))

    existing = {
        p for (p,) in await db.execute(
            select(MemoryFile.path).where(
                MemoryFile.user_id == user_id,
                MemoryFile.path.startswith(coll.root + "/"),
            )
        )
    }
    # Never silently overwrite a member that already exists — after a first run
    # those are this migration's own output, and a second run must be a no-op
    # rather than a re-import of whatever the monolith still says.
    already = sorted(p for p in existing if p in {m.path for m in plan.members})
    report["already_present"] = already

    written: list[str] = []
    if not dry_run:
        for m in plan.members:
            if m.path in existing:
                continue
            await commit_file(
                db, user_id=user_id, path=m.path, content=m.content,
                expected_updated_at=None,
                request_id=request_id or "memory-split",
            )
            written.append(m.path)
        logger.info(
            "memory_split_applied",
            extra={"user_id": user_id, "source": coll.split_from,
                   "written": len(written), "problems": len(plan.problems)},
        )

    report["written"] = written
    report["status"] = "planned" if dry_run else "applied"

    n, probs = len(plan.members), len(plan.problems)
    skipped = f", {len(already)} already present" if already else ""
    if dry_run:
        report["verdict"] = (
            f"Would write {n - len(already)} {coll.entity_noun} file(s) from "
            f"{report['source_bytes'] / 1024:.1f}K{skipped}"
            + (f"; {probs} problem(s) need a decision first." if probs
               else ". No problems found.")
        )
    else:
        report["verdict"] = (
            f"Wrote {len(written)} {coll.entity_noun} file(s){skipped}. "
            f"{coll.split_from} is unchanged and now read-only for agents."
            + (f" {probs} entity(ies) were skipped — see problems." if probs else "")
        )
    return report


async def split_all(db, user_id: int, *, dry_run: bool = True, request_id: str = "") -> dict:
    """Every collection that declares a source document."""
    from app.services.memory_spec import COLLECTIONS

    reports = [
        await apply_split(db, user_id, c, dry_run=dry_run, request_id=request_id)
        for c in COLLECTIONS if c.split_from
    ]
    return {
        "dry_run": dry_run,
        "collections": reports,
        "verdict": " ".join(r["verdict"] for r in reports),
    }
