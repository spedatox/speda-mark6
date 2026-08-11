"""
The grammar of every memory document (docs/MEMORY_ARCHITECTURE_V4.md §3.1).

Until now no memory file declared a shape, so no write could be checked against
one. That absence is the single cause behind every defect found when the store
was read end to end: a corrupted generation persisted for three weeks, a
paragraph written above the H1, two documents concatenated into one file,
Erasmus notes filed under an HTML-template heading, one person at `##` among
peers at `###`.

A spec says what a document IS: its kind, who may write it, what sections may
exist at what level, and what a valid entry inside them looks like. It is
deliberately coarse — it constrains STRUCTURE, not prose. Whether a claim is
true stays the writer's problem; whether it landed in a section that exists is
ours, and that is the half a prompt cannot enforce.

Specs are written from the owner's actual files. When a document legitimately
grows a new section, widen its spec in the same commit — a spec that lags the
document turns the verifier into noise, and a noisy verifier gets ignored, which
is how the store ended up unprotected in the first place.
"""

from dataclasses import dataclass, field

# ── Kinds (v4 §2) ─────────────────────────────────────────────────────────────

NARRATIVE = "narrative"      # prose chapters, read whole
REGISTRY = "registry"        # one section per entity, fixed inner schema
LEDGER = "ledger"            # indexed by time or topic, tables carry the payload
OBSERVATIONS = "observations"  # flat, attributed, atomic
SYSTEM = "system"            # machine-written trails; no owner knowledge

KINDS = (NARRATIVE, REGISTRY, LEDGER, OBSERVATIONS, SYSTEM)


@dataclass(frozen=True)
class DocumentSpec:
    path: str
    kind: str
    summary: str
    # The only agent that may write it. None = shared across the roster.
    # Recorded from the revision trail, not invented: academic.md was written by
    # ultron 19 times and nobody else, wellness.md by atomix 11 of 12.
    owner_agent: str | None = None
    # Injected into every turn's system prompt. Four files are, and they are the
    # ones whose size is billed on every request by every agent.
    injected: bool = False
    # Top-level (`##`) sections the document is allowed to have. Empty = any.
    # A section outside this set is where content bleeding starts.
    sections: tuple[str, ...] = ()
    # Sections that must be present. Losing one is how dossier.md's
    # "Explicit prohibitions" — hard behavioural rules — silently disappeared.
    required: tuple[str, ...] = ()
    # For registries: the heading level entities live at (2 or 3). social.md is
    # two-level (category → person); projects.md is one.
    entity_level: int | None = None
    # For ledgers: a regex that index sections must match, e.g. a month or a date.
    index_pattern: str | None = None
    # Heading level the index lives at.
    index_level: int = 2
    # For ledgers whose index sits below a fixed parent — wellness.md's sessions
    # live at `### YYYY-MM-DD` under `## 5. LOG (Chronological)`, not at the top.
    index_parent: str | None = None
    # How entries are carried under an index key: a markdown table with fixed
    # columns, or dated bullets. `ledger_append` needs to know which, because
    # appending a table row to a bullet log produces something no reader expects.
    entry_style: str = "bullets"          # "table" | "bullets"
    # Column headers per sub-section, for table ledgers. A row with the wrong
    # number of cells is the ledger equivalent of a type error.
    columns: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # Sub-sections a table ledger keeps under each index key.
    entry_sections: tuple[str, ...] = ()
    # Byte ceiling. Injected files are billed on every request, so theirs is
    # tighter than a file only read on demand.
    max_bytes: int = 60_000
    # Free-text notes surfaced in verifier output.
    notes: str = ""
    aliases: tuple[str, ...] = field(default_factory=tuple)
    # Set when a document has been superseded by a COLLECTION but has not been
    # split yet. The file stays fully live — writes work, checks run — and the
    # verifier merely says where it is going. Deployment is GitOps (push to main
    # rewrites the server), so this code lands on prod BEFORE anyone runs the
    # split; a spec that assumed the migration had happened would break every
    # write to a 38 KB file in the window between the two.
    superseded_by: str = ""


# ── Collections: one entity per file (v4 §2.2, extended) ──────────────────────
#
# A REGISTRY is a set of independent entities that happens to be stored in one
# document. That storage choice is what made `projects.md` 38 KB and `social.md`
# 27 KB, and it costs on the read path: the memory tool has no section-addressed
# read, so answering "what's the stack on Mark VI" fetches all 38 KB (~10k
# tokens) and then re-sends it on every following iteration of the agentic loop.
# One entity per file makes that read ~1k, and it also removes the write hazard
# the verifier exists to catch — a `str_replace` anchored on a common phrase can
# only damage the one entity it is scoped to.
#
# Only REGISTRY documents split. Ledgers must NOT: `finance.md` is already
# indexed by `## YYYY-MM`, and splitting it into a file per month turns "compare
# July with August" into N reads and cuts repayment schedules in half. That is
# the same mistake v3 made by shredding it into sentences (v4 §2.3), one storey
# down.


@dataclass(frozen=True)
class CollectionSpec:
    """A directory whose members all share one grammar.

    `depth` is the number of path segments below `root` before the file:
      1 → /memories/projects/<slug>.md
      2 → /memories/social/<group>/<slug>.md
    Two levels exist for social.md because the document has two — `## Category`
    then `### Person`. Flattening it would lose the grouping that makes the
    injected directory listing readable, and reading a CATEGORY as a person is
    the exact defect v4 §2.2 records.
    """

    root: str
    kind: str
    summary: str
    entity_noun: str                            # "project" / "person"
    depth: int = 1
    groups: tuple[str, ...] = ()                # allowed folders when depth == 2
    sections: tuple[str, ...] = ()              # allowed `##` inside a member
    required: tuple[str, ...] = ()
    markers: tuple[str, ...] = ()               # literal strings every member carries
    max_bytes: int = 8_000
    owner_agent: str | None = None
    split_from: str = ""                        # the monolith it replaces
    entity_level: int = 2                       # heading level entities sat at there
    notes: str = ""


COLLECTIONS: tuple[CollectionSpec, ...] = (
    CollectionSpec(
        root="/memories/projects",
        kind=REGISTRY,
        summary="One project per file — Status/Stack/Architecture/Team",
        entity_noun="project",
        depth=1,
        # Deliberately open. The real projects.md is 38 KB the migration has not
        # read yet, and a section list guessed from a spec document rather than
        # from the file is what makes a verifier cry wolf — the failure mode
        # memory_spec's own header warns about. The splitter reports the section
        # names it actually found; tighten this from that output, not from here.
        sections=(),
        max_bytes=8_000,
        split_from="/memories/projects.md",
        entity_level=2,
        notes="Filename is the slug of the H1. Sub-detail at `##`.",
    ),
    CollectionSpec(
        root="/memories/social",
        kind=REGISTRY,
        summary="One person per file, grouped by category",
        entity_noun="person",
        depth=2,
        groups=("professional", "siberay-board", "personal"),
        # People carry `**Who:**` / `**Events:**` as bold markers, not headings,
        # so there is nothing to put in `sections` — the check is on the markers.
        markers=("**Who:**", "**Events:**"),
        max_bytes=8_000,
        split_from="/memories/social.md",
        entity_level=3,
        notes="**Who:** paragraph, then **Events:** dated bullets, newest first.",
    ),
)

_COLLECTIONS_BY_ROOT = {c.root: c for c in COLLECTIONS}


# Turkish. `unicodedata` alone is not enough: `ı` and `ğ` have no compatibility
# decomposition, so NFKD leaves them intact and they end up percent-mangled or
# dropped. "Pınar Uzun" and "Doğan" are real entries — they have to round-trip.
_SLUG_MAP = str.maketrans({
    "ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
    "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c",
    "â": "a", "î": "i", "û": "u", "é": "e", "ñ": "n", "ß": "ss",
})


def slugify(title: str) -> str:
    """Entity title → filename stem. Deterministic and stable: the slug is the
    file's identity, so the same name must always produce the same path."""
    import re as _re

    s = title.strip().translate(_SLUG_MAP).lower()
    s = _re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "untitled"


def collection_for(path: str) -> CollectionSpec | None:
    """The collection a member path belongs to, or None."""
    for coll in COLLECTIONS:
        prefix = coll.root + "/"
        if not path.startswith(prefix) or not path.endswith(".md"):
            continue
        rest = path[len(prefix):]
        if rest.count("/") != coll.depth - 1:
            continue
        if coll.depth == 2 and coll.groups:
            group = rest.split("/", 1)[0]
            if group not in coll.groups:
                return None
        return coll
    return None


def member_path(coll: CollectionSpec, title: str, group: str | None = None) -> str:
    """Where an entity's file belongs. The one function that decides this, so a
    write and a read can never disagree about where a person lives."""
    stem = slugify(title)
    if coll.depth == 1:
        return f"{coll.root}/{stem}.md"
    if not group:
        raise ValueError(f"{coll.root} needs a group ({', '.join(coll.groups)})")
    g = slugify(group)
    if coll.groups and g not in coll.groups:
        raise ValueError(
            f"{group!r} is not a category of {coll.root} "
            f"({', '.join(coll.groups)})"
        )
    return f"{coll.root}/{g}/{stem}.md"


def _member_spec(coll: CollectionSpec, path: str) -> DocumentSpec:
    """Synthesize the DocumentSpec for one member.

    Returning a real DocumentSpec is what keeps this change small: `check_write`,
    `verify_document` and `registry_upsert` all call `spec_for()` and are handed
    the same type they always were. None of them learns that collections exist.

    `entity_level=None` on purpose — in a member file the entity IS the H1, so
    the "all entities sit at one heading level" check does not apply and
    `check_member_title` checks the H1 against the filename instead.
    """
    return DocumentSpec(
        path=path,
        kind=coll.kind,
        summary=f"{coll.entity_noun.capitalize()}: {path.rsplit('/', 1)[-1][:-3]}",
        owner_agent=coll.owner_agent,
        injected=False,
        sections=coll.sections,
        required=coll.required,
        entity_level=None,
        max_bytes=coll.max_bytes,
        notes=coll.notes,
    )


# ── The store, as it actually is ──────────────────────────────────────────────

SPECS: dict[str, DocumentSpec] = {
    # ── Shared, cross-agent ──────────────────────────────────────────────────
    "/memories/owner.md": DocumentSpec(
        path="/memories/owner.md",
        kind=NARRATIVE,
        summary="Who he is — biography in chapters, plus employment history",
        injected=True,
        max_bytes=24_000,
        sections=(
            "DOSSIER: Ahmet Erol Bayrak — Complete Personal History",
            "Origins", "The Uludağ Years", "The Istanbul Summer (2024)",
            "The Devastation and Ankara Arrival (September 2024)",
            "The Job Hunt and English Time (Late 2024)",
            "The Web of Lies (February–August 2025)",
            "Kurtulus Park and the Turn (September 2025)",
            "Employment History", "Communication style", "Explicit instructions",
        ),
        required=("Employment History",),
        notes="Chapters are chronological. New chapters are appended, not inserted.",
    ),
    "/memories/dossier.md": DocumentSpec(
        path="/memories/dossier.md",
        kind=OBSERVATIONS,
        summary="Observed preferences, behavioural prohibitions, trusted contacts",
        injected=True,
        max_bytes=12_000,
        sections=(
            "Likes / responds well to", "Dislikes / friction",
            "Wants — and in what manner", "Brainstorm Mode",
            "Explicit prohibitions", "Trusted contacts & services",
            "Open questions",
        ),
        # These two govern how every agent answers. v3 collapsed all six sections
        # into one called "General" and a hard prohibition became
        # indistinguishable from a mild preference.
        required=("Explicit prohibitions", "Dislikes / friction"),
    ),
    "/memories/current.md": DocumentSpec(
        path="/memories/current.md",
        kind=OBSERVATIONS,
        summary="What is true in the owner's life right now",
        injected=True,
        max_bytes=6_000,
        notes="A snapshot, not a log. Something that ends moves to history.md.",
    ),
    "/memories/history.md": DocumentSpec(
        path="/memories/history.md",
        kind=OBSERVATIONS,
        summary="Mark VI-era states that have ended",
        injected=True,
        max_bytes=12_000,
    ),
    "/memories/social.md": DocumentSpec(
        path="/memories/social.md",
        kind=REGISTRY,
        summary="People, grouped by category — Who block + dated Events each",
        # Two-level: `## Category` then `### Person`. Reading the CATEGORY as a
        # person is exactly what produced `person:Professional` with 21 facts.
        entity_level=3,
        sections=("Professional", "Siberay Board", "Personal"),
        max_bytes=48_000,
        notes="Every person needs a **Who:** block; events are dated bullets, newest first.",
        superseded_by="/memories/social",
    ),
    "/memories/projects.md": DocumentSpec(
        path="/memories/projects.md",
        kind=REGISTRY,
        summary="Every project, with Status/Stack/Architecture/Team",
        entity_level=2,
        max_bytes=60_000,
        notes="One `## Project` per project; sub-detail at `###`.",
        superseded_by="/memories/projects",
    ),
    "/memories/log.md": DocumentSpec(
        path="/memories/log.md",
        kind=SYSTEM,
        summary="Rolling one-line session summaries",
        max_bytes=8_000,
        notes="System-written. Bounded to the most recent entries.",
    ),

    # ── Agent-owned domain documents ─────────────────────────────────────────
    "/memories/finance.md": DocumentSpec(
        path="/memories/finance.md",
        kind=LEDGER,
        summary="Monthly ledger — incomes, expenses, debts, repayment schedules",
        owner_agent="sentinel",
        index_pattern=r"^\d{4}-\d{2}$",
        index_level=2,
        entry_style="table",
        entry_sections=("Incomes", "Expenses", "Debts"),
        columns={
            "Incomes": ("Date", "Source", "Amount (TL)", "Notes"),
            "Expenses": ("Date", "Item", "Amount (TL)", "Notes"),
            "Debts": ("Debt", "Amount (TL)", "Status", "Notes"),
        },
        sections=(
            "Monthly structure", "Notes", "Scholarships & Loans (Reference)",
            "BLACKWALNUT — Continuous Wealth Strategies",
        ),
        max_bytes=48_000,
        notes=(
            "`## YYYY-MM` per month, then `### Incomes / Expenses / Debts` tables. "
            "A figure that changes is a new row, not an edited one."
        ),
    ),
    "/memories/wellness.md": DocumentSpec(
        path="/memories/wellness.md",
        kind=LEDGER,
        summary="Training protocol, athlete profile and session log",
        owner_agent="atomix",
        index_pattern=r"^\d{4}-\d{2}-\d{2}",
        index_level=3,
        index_parent="5. LOG (Chronological)",
        entry_style="bullets",
        # Section names are copied from the document, not from what they ought to
        # be called. A spec written from memory rather than from the file fires
        # false errors, and a verifier that cries wolf gets switched off.
        sections=(
            "1. SYSTEM DIRECTIVES & OUTPUT RULES", "2. ATHLETE PROFILE & STATUS",
            "3. ACTIVE PROGRAM & BENCHMARKS", "4. GYM ENVIRONMENT & EQUIPMENT",
            "5. LOG (Chronological)",
        ),
        required=("5. LOG (Chronological)",),
        max_bytes=60_000,
        notes="Sessions are `### YYYY-MM-DD — <split> · <status>` under `## 5. LOG`, newest first.",
    ),
    "/memories/academic.md": DocumentSpec(
        path="/memories/academic.md",
        kind=LEDGER,
        summary="Academic calendar, KPSS preparation, course materials, standing",
        owner_agent="ultron",
        sections=(
            "KPSS 2026", "Akademik Takvim", "Ders Materyalleri",
            "Erasmus+", "Session Durumu", "Akademik Durum",
        ),
        max_bytes=32_000,
        notes=(
            "Absorbed kpss.md — the exam tracker is a section here, not its own "
            "document. Keep each top-level section self-contained; this file "
            "previously held four documents and a second H1."
        ),
    ),
    "/memories/cybersec.md": DocumentSpec(
        path="/memories/cybersec.md",
        kind=LEDGER,
        summary="Cybersecurity learning journey",
        owner_agent="centurion",
        sections=("Structure", "Tracks", "Progress Log", "Resources",
                  "Certifications / Goals"),
        max_bytes=24_000,
    ),
    "/memories/ops.md": DocumentSpec(
        path="/memories/ops.md",
        kind=LEDGER,
        summary="Runbook and action log for the host",
        owner_agent="orion",
        sections=("Part 1: Self-Guide — Service Map & Procedures",
                  "Part 2: Action Log"),
        index_pattern=r"^\d{4}-\d{2}-\d{2}",
        index_level=3,
        index_parent="Part 2: Action Log",
        entry_style="bullets",
        max_bytes=32_000,
        notes=(
            "Operational facts go stale silently and are then acted on. Every "
            "claim about the running system carries the date it was verified."
        ),
    ),
}

# Retired documents. Present so the verifier can say "this should not exist"
# rather than "unknown file", and so a re-seed never resurrects one.
RETIRED: dict[str, str] = {
    "/memories/sessions.md": "superseded by wellness.md (same document continued)",
    "/memories/kpss.md": "merged into academic.md — the exam tracker is a section there",
}


def spec_for(path: str) -> DocumentSpec | None:
    """The grammar for one path — an exact document, or a collection member.

    Exact match wins, so `/memories/projects.md` keeps its own spec for as long
    as the monolith exists and only `/memories/projects/<slug>.md` resolves
    through the collection.
    """
    exact = SPECS.get(path)
    if exact is not None:
        return exact
    coll = collection_for(path)
    return _member_spec(coll, path) if coll else None


def is_collection_root(path: str) -> bool:
    return path.rstrip("/") in _COLLECTIONS_BY_ROOT


def collection_by_root(root: str) -> CollectionSpec | None:
    return _COLLECTIONS_BY_ROOT.get(root.rstrip("/"))


def collection_from_monolith(path: str) -> CollectionSpec | None:
    """The collection that replaces a pre-split document, if any."""
    for coll in COLLECTIONS:
        if coll.split_from == path:
            return coll
    return None


def is_retired(path: str) -> bool:
    return path in RETIRED


def owner_of(path: str) -> str | None:
    spec = spec_for(path)
    return spec.owner_agent if spec else None


def injected_paths() -> tuple[str, ...]:
    return tuple(p for p, s in SPECS.items() if s.injected)


def collection_summaries() -> dict[str, str]:
    """Collection roots for the canonical taxonomy and the systems board."""
    return {c.root: c.summary for c in COLLECTIONS}
