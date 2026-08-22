"""
Structural validation for writes to the /memories virtual filesystem.

docs/MEMORY_ARCHITECTURE.md declares a strict file law — a closed taxonomy, one
question per file, dated and attributed entries, a Who/Events schema for people.
Until now that law lived entirely in prose: `prompts/core/08_memory.md` asked the
agents nicely and nothing checked the result. §1 of that document records what
happened next ("facts drift between files", "new file types were added ad hoc
with no declared schema"). A contract enforced only by prompt is a contract that
erodes at exactly the rate the model is having a bad day.

This module is the enforcement half, ported from Honcho's peer-card validator
(plastic-labs/honcho, `_validate_peer_card_entry`), which rejects a card entry
structurally — allowed prefix, non-empty body, length cap — and leaves the
question of whether the CLAIM is right to the prompt. Same split here: form is
checked in code, substance stays with the agent.

Two rules keep it safe to switch on against a live store:

  1. **Only newly-introduced violations are hard errors.** A write is checked as
     a DELTA (before → after). Existing mess in a file never blocks an unrelated
     edit; the write only has to avoid making things worse. This is what lets
     enforcement land before Orion's audit has cleaned anything.
  2. **The owner is never blocked.** §4.3 makes owner commits ground truth. His
     writes are inspected and reported, never rejected — Orion re-files them.
"""

import re

# ── The closed taxonomy (docs/MEMORY_ARCHITECTURE.md §2) ──────────────────────
# Imported from memory_store so the canonical set has exactly one definition.
from app.services.memory_store import AUDIT_ROOT, CANONICAL_FILES

# Per-file byte cap. Four of these files are injected into EVERY turn's system
# prompt for EVERY agent, so unbounded growth is not a tidiness concern — it is a
# per-request bill. Honcho's equivalent is the 40-entry peer-card cap; ours is
# sized in bytes because our unit is a markdown file, not a list.
INJECTED_FILE_MAX_BYTES = 12_000     # owner/current/dossier/history + agent source files
ONDEMAND_FILE_MAX_BYTES = 40_000     # read via the tool, so only the read pays

_INJECTED_PATHS = frozenset({
    "/memories/owner.md",
    "/memories/current.md",
    "/memories/dossier.md",
    "/memories/history.md",
    "/memories/patterns.md",
})

# `- [2026-07-06, sentinel] wants totals before breakdowns.` — the dossier's
# attribution stamp. Multiple observers merge into one entry (§ Pass 3), so the
# agent list is comma/plus separated.
_DOSSIER_ENTRY = re.compile(r"^\s*[-*]\s+")
_DOSSIER_ATTRIBUTION = re.compile(r"^\s*[-*]\s+\[\d{4}-\d{2}-\d{2},\s*[a-z0-9_+ ]+\]")

_DOSSIER_SECTIONS = (
    "## Likes / responds well to",
    "## Dislikes / friction",
    "## Wants — and in what manner",
    "## Open questions",
)

# Relative dates that will be meaningless the moment the file is re-read. Orion's
# Pass 3 converts these; catching them at write time means he has less to fix.
_RELATIVE_DATES = re.compile(
    r"\b(next (week|month|year|monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    r"|last (week|month|year)|this (week|month|year)"
    r"|tomorrow|yesterday|recently|soon|in a few (days|weeks|months))\b",
    re.IGNORECASE,
)

# Dates in any shape other than ISO. Normalising these is Orion's Pass 3; warning
# here keeps the drift from accumulating between audits.
_NON_ISO_DATE = re.compile(r"\b\d{1,2}[/.]\d{1,2}[/.]\d{2,4}\b")


class MemorySchemaViolation(Exception):
    """A write would newly break the file law.

    The message is written FOR THE MODEL and returned as the tool result: it must
    name the rule, what was wrong, and what to do instead. A rejection the model
    cannot act on is just a retry loop.
    """


def is_canonical(path: str) -> bool:
    """Whether this path is a legitimate place for owner knowledge.

    The taxonomy stays closed — a brand-new top-level file is still the move
    that produced the v1 drift. It is closed over a set of DOCUMENTS *and* a set
    of COLLECTIONS: a new file inside `/memories/projects/` is not a new kind of
    document, it is one more instance of a kind that already has a spec. Without
    this branch the closed-taxonomy rule below rejects every project and every
    person the owner ever acquires again.
    """
    if path in CANONICAL_FILES:
        return True
    from app.services.memory_spec import collection_for

    return collection_for(path) is not None


def is_system_path(path: str) -> bool:
    return path.startswith(AUDIT_ROOT) or "/." in path


def max_bytes_for(path: str) -> int:
    """The cap for one path. Injected files are billed on every request by every
    agent, so theirs is tightest; a collection member is one entity and has no
    business being large, which is the point of splitting the registry at all."""
    if path in _INJECTED_PATHS:
        return INJECTED_FILE_MAX_BYTES
    from app.services.memory_spec import collection_for

    coll = collection_for(path)
    return coll.max_bytes if coll else ONDEMAND_FILE_MAX_BYTES


# ── Per-file structural checks ────────────────────────────────────────────────

def _dossier_violations(content: str) -> list[str]:
    """Every dossier bullet carries `[YYYY-MM-DD, agent_id]` (§2.2). Unattributed
    observations are what make the file impossible to merge or age out."""
    problems: list[str] = []
    for line in content.splitlines():
        if _DOSSIER_ENTRY.match(line) and not _DOSSIER_ATTRIBUTION.match(line):
            problems.append(
                f"dossier.md entry is missing its `[YYYY-MM-DD, agent_id]` "
                f"attribution stamp: {line.strip()[:80]}"
            )
    missing = [s for s in _DOSSIER_SECTIONS if s not in content]
    if missing:
        problems.append(
            "dossier.md must keep exactly its four sections — missing: "
            + ", ".join(missing)
        )
    return problems


def _social_violations(content: str) -> list[str]:
    """Each person section carries a Who block and an Events log (§2.2).

    People live at `###` — `##` is the CATEGORY level (memory_spec: social.md is
    `entity_level=3`). This walked `##` and so asked "Professional", "Personal"
    and "Siberay Board" for a `**Who:**` block they were never meant to have.
    Delta-checking hid it: the noise was pre-existing on every write, so it never
    blocked anything and never surfaced.
    """
    problems: list[str] = []
    # Walk lines rather than splitting: a split loses the heading text, and the
    # person's name is what makes the violation message actionable.
    current: str | None = None
    body: list[str] = []
    blocks: list[tuple[str, str]] = []
    for line in content.splitlines():
        if line.startswith("### "):
            if current:
                blocks.append((current, "\n".join(body)))
            current, body = line[4:].strip(), []
        elif line.startswith("## "):
            # A new category closes the person before it.
            if current:
                blocks.append((current, "\n".join(body)))
            current, body = None, []
        elif current:
            body.append(line)
    if current:
        blocks.append((current, "\n".join(body)))

    for name, block in blocks:
        if not block.strip():
            continue
        if "**Who:**" not in block:
            problems.append(
                f"social.md section '{name}' has no `**Who:**` block — every person "
                f"needs one line saying who they are to the owner."
            )
        if "**Events:**" not in block:
            problems.append(
                f"social.md section '{name}' has no `**Events:**` log — dated entries, "
                f"newest first."
            )
    return problems


def _current_violations(content: str) -> list[str]:
    """current.md is a 3–10 bullet snapshot (§2.2). More than that and it has
    stopped being a snapshot; fewer and it is not carrying the present tense."""
    bullets = [l for l in content.splitlines() if re.match(r"^\s*[-*]\s+\S", l)]
    if len(bullets) > 12:
        return [
            f"current.md has {len(bullets)} bullets — it is a 3–10 bullet snapshot "
            f"of what is active NOW. Demote what has ended to history.md instead of "
            f"letting the snapshot grow."
        ]
    return []


def _member_violations(path: str, content: str) -> list[str]:
    """One entity, one file — the markers its collection says every member has.

    Cheaper to enforce than the monolith version was: there is exactly one entity
    per file, so a missing `**Who:**` is unambiguous. In the 27 KB document the
    same question depended on correctly telling a category heading from a person
    heading, which is precisely what nothing did reliably.
    """
    from app.services.memory_spec import collection_for

    coll = collection_for(path)
    if coll is None or not coll.markers:
        return []

    name = path.rsplit("/", 1)[-1][:-3]
    for line in content.splitlines():
        if line.startswith("# "):
            name = line[2:].strip()
            break

    problems = []
    for marker in coll.markers:
        if marker not in content:
            problems.append(
                f"{path} ({coll.entity_noun} {name!r}) has no `{marker}` block — "
                f"every {coll.entity_noun} file needs one. {coll.notes}"
            )
    return problems


# `- [2026-08-22, ultron, medium] plans made after a night shift slip → schedule
# the light block first.` Same attribution stamp as the dossier plus a
# confidence, because a pattern is INDUCED and can be wrong, and one more thing:
# the arrow. A line with no countermeasure is an observation that wandered into
# the wrong document — the record already holds those, findable by meaning, and
# this file is only worth its place in every prompt if every line tells an agent
# what to DO before the pattern fires.
_PATTERN_ENTRY = re.compile(
    r"^\s*[-*]\s+\[\d{4}-\d{2}-\d{2},\s*[a-z0-9_+ ]+,\s*(high|medium|low)\]"
)
_PATTERN_ARROW = ("→", "->")


def _patterns_violations(content: str) -> list[str]:
    """Every entry is attributed, carries a confidence, and names its move."""
    problems: list[str] = []
    for i, line in enumerate(content.splitlines(), 1):
        if not _DOSSIER_ENTRY.match(line):
            continue
        if not _PATTERN_ENTRY.match(line):
            problems.append(
                f"patterns.md line {i} is missing its "
                f"`[YYYY-MM-DD, agent_id, confidence]` stamp (confidence is one of "
                f"high/medium/low — 'high' for 5+ supporting facts, 'medium' for "
                f"3-4, 'low' for 2). An induced claim without its strength reads "
                f"as a certainty, and this file is acted on before anyone checks."
            )
            continue
        if not any(a in line for a in _PATTERN_ARROW):
            problems.append(
                f"patterns.md line {i} states a pattern but not the move it calls "
                f"for. Write `pattern → what to do about it`. A pattern with no "
                f"countermeasure belongs in the observation record, not in the "
                f"block every agent is billed for on every turn."
            )
    return problems


_FILE_CHECKS = {
    "/memories/patterns.md": _patterns_violations,
    "/memories/dossier.md": _dossier_violations,
    "/memories/social.md": _social_violations,
    "/memories/current.md": _current_violations,
}


def _structural_violations(path: str, content: str) -> list[str]:
    check = _FILE_CHECKS.get(path)
    if check:
        return check(content)
    return _member_violations(path, content)


def _soft_warnings(path: str, content: str) -> list[str]:
    warnings: list[str] = []
    for match in set(m.group(0) for m in _RELATIVE_DATES.finditer(content)):
        warnings.append(
            f"'{match}' is a relative date — it stops being true the moment this "
            f"file is re-read. Write the absolute date instead."
        )
    for match in set(m.group(0) for m in _NON_ISO_DATE.finditer(content)):
        warnings.append(f"'{match}' is not an ISO date — use YYYY-MM-DD.")
    return warnings[:6]   # a wall of warnings is a wall the model skips


# ── The write gate ────────────────────────────────────────────────────────────

def check_write(
    *,
    path: str,
    before: str,
    after: str,
    is_create: bool,
    author: str,
) -> list[str]:
    """
    Inspect one pending write. Returns a list of ADVISORY warnings; raises
    MemorySchemaViolation when the write would newly break the file law.

    `before` is "" for a create. `author` is the agent_id, or "owner" for a
    systems-board commit — the owner is inspected but never blocked (§4.3).
    """
    is_owner = author == "owner"

    # System trails are Orion's plumbing, not owner knowledge. No taxonomy.
    if is_system_path(path):
        return []

    # v3: derived surfaces are not writable by hand. An edit here would survive
    # until the next render and then disappear without a trace, which is a worse
    # outcome than a refusal — the agent would believe it had filed the fact.
    # The refusal names the tool that actually works, so the turn can recover.
    from app.services.memory_compose import COMPOSED_FILES
    from app.services.memory_render import RENDERED_FILES

    if not is_owner and (path in RENDERED_FILES or path in COMPOSED_FILES):
        kind = "rendered from" if path in RENDERED_FILES else "composed from"
        raise MemorySchemaViolation(
            f"Write rejected — `{path}` is {kind} the observation record and is not "
            f"editable as text. Anything written here is overwritten on the next "
            f"render, so nothing was saved and nothing would have been.\n\n"
            f"Record the FACT instead, with `record_observation`. Give it a "
            f"`subject` (owner / person:<Name> / project:<Name>) and a `domain`, "
            f"and it will appear on the right surface automatically — including "
            f"this one. To end something that stopped being true, record it with "
            f"a `valid_until` rather than deleting the line."
        )

    hard: list[str] = []

    # ── Ownership: an agent writes its own document, not a colleague's ───────
    #
    # Declared in memory_spec from the revision trail, and until now only
    # declared. The trail shows it was already breached: speda wrote finance.md
    # six times and ops.md once — documents owned by sentinel and orion. That is
    # not vandalism, it is an agent being helpful in a lane it does not know, and
    # it is how a ledger acquires rows in a format its owner does not maintain.
    #
    # Reading stays open to everyone; only writing is scoped. The owner and Orion
    # are exempt — the first is ground truth, the second is the custodian whose
    # whole job is repairing other people's documents.
    from app.services.memory_spec import owner_of

    document_owner = owner_of(path)
    if document_owner and author not in (document_owner, "owner", "orion"):
        raise MemorySchemaViolation(
            f"Write rejected — `{path}` belongs to {document_owner}, not to you.\n\n"
            f"Nothing was saved. If the fact is yours to record, put it in your own "
            f"document or in the shared ones (current.md, social.md, projects.md, "
            f"dossier.md). If it genuinely belongs here, hand it to {document_owner} "
            f"with `dispatch_agent` — a ledger kept by one hand stays readable, and "
            f"one kept by five does not."
        )

    # ── A split registry is written in its new home, not its old one ─────────
    #
    # Deployment is GitOps, so this code reaches prod before anyone runs the
    # split, and the two stores would otherwise be writable at the same time.
    # That is the v1 failure exactly — "facts drift between files" — and it is
    # worse here because both files would look authoritative.
    #
    # Freezing the monolith on DEPLOY rather than on migration closes the window
    # completely: new knowledge only ever has one home, whether or not the split
    # has run yet. Reads keep working, so nothing is lost in the meantime.
    # Orion is exempt because repairing documents is his job, and the owner is
    # never blocked (§4.3).
    from app.services.memory_spec import collection_from_monolith, member_path

    superseded = collection_from_monolith(path)
    if superseded is not None and author not in ("owner", "orion"):
        if superseded.depth == 2:
            example = f"{superseded.root}/<category>/<name>.md"
            groups = f" Categories: {', '.join(superseded.groups)}." if superseded.groups else ""
        else:
            example = member_path(superseded, "Example Name")
            groups = ""
        raise MemorySchemaViolation(
            f"Write rejected — `{path}` has been split into one file per "
            f"{superseded.entity_noun} under `{superseded.root}/`, and is now "
            f"read-only. Nothing was saved.\n\n"
            f"Write the {superseded.entity_noun}'s own file instead: `{example}`."
            f"{groups} Use `create` if it does not exist yet — the folder is part "
            f"of the taxonomy, so a new {superseded.entity_noun} there is expected, "
            f"not a new file type. Reading `{path}` still works while the "
            f"migration finishes."
        )

    # ── The verifier (app/services/memory_verify.py) ─────────────────────────
    # Delta-only: a document with pre-existing problems must stay editable or
    # nothing could ever be repaired. Errors this write ADDS are refused;
    # warnings ride back as advice on a successful write.
    from app.services.memory_verify import introduced_by

    new_findings = introduced_by(path, before, after)
    hard.extend(
        f"{f.message} ({f.rule}"
        + (f", line {f.line}" if f.line else "")
        + f"). {f.fix}"
        for f in new_findings if f.severity == "error"
    )
    verifier_warnings = [
        f"{f.message} — {f.fix}" for f in new_findings if f.severity == "warning"
    ]

    # 1. The taxonomy is closed (§2). A brand-new top-level file is the exact
    #    move that produced the v1 drift ("new file types were added ad hoc").
    if is_create and not is_canonical(path):
        from app.services.memory_spec import COLLECTIONS

        # A path that ALMOST landed in a collection is the common miss — a person
        # filed under a category that does not exist. Say which categories do,
        # rather than reciting the whole taxonomy at someone who had the right
        # idea and the wrong folder.
        near = next(
            (c for c in COLLECTIONS if path.startswith(c.root + "/")), None
        )
        if near is not None:
            detail = (
                f"`{path}` is inside `{near.root}` but not where a {near.entity_noun} "
                f"goes. The shape is `{near.root}/"
                + ("<category>/" if near.depth == 2 else "")
                + "<slug>.md`"
                + (
                    f", and the categories are: {', '.join(near.groups)}."
                    if near.groups else "."
                )
            )
        else:
            detail = (
                f"`{path}` is not a canonical memory file. Route this fact into the "
                f"file that answers its question: a person → `/memories/social/"
                f"<category>/<name>.md`, a project → `/memories/projects/<name>.md`, "
                f"an active life state → current.md, an observed preference → "
                f"dossier.md, pre-Mark-VI biography → owner.md. If it genuinely "
                f"needs a new top-level file, the owner creates it from the "
                f"systems board."
            )
        hard.append(f"The /memories taxonomy is closed — {detail}")

    # 2. Size. Only enforced on GROWTH — an already-oversized file must stay
    #    editable, or Orion could never compress it back under the cap.
    cap = max_bytes_for(path)
    after_size = len(after.encode("utf-8"))
    before_size = len(before.encode("utf-8"))
    if after_size > cap and after_size > before_size:
        where = "injected into every turn" if path in _INJECTED_PATHS else "read on demand"
        hard.append(
            f"`{path}` would grow to {after_size / 1024:.1f}K, over its {cap / 1024:.0f}K "
            f"cap ({where}). Compress or demote before adding — do not let the file "
            f"grow past the point where it is cheap to carry."
        )

    # 3. Per-file schema, delta-only: a violation that already existed is Orion's
    #    to clean, not this write's to be blamed for.
    existing = set(_structural_violations(path, before)) if before else set()
    introduced = [v for v in _structural_violations(path, after) if v not in existing]
    hard.extend(introduced)

    if hard and not is_owner:
        raise MemorySchemaViolation(
            "Write rejected — it would break the memory file law:\n  - "
            + "\n  - ".join(hard)
            + "\n\nFix the content and write again. Nothing was saved."
        )

    warnings = verifier_warnings + _soft_warnings(path, after)
    if hard and is_owner:
        # Ground truth lands regardless; Orion re-files it and the trail records why.
        warnings = [f"(owner commit, accepted as-is) {h}" for h in hard] + warnings
    return warnings
