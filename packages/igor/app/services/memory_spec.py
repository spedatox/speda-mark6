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
    # Byte ceiling. Injected files are billed on every request, so theirs is
    # tighter than a file only read on demand.
    max_bytes: int = 60_000
    # Free-text notes surfaced in verifier output.
    notes: str = ""
    aliases: tuple[str, ...] = field(default_factory=tuple)


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
    ),
    "/memories/projects.md": DocumentSpec(
        path="/memories/projects.md",
        kind=REGISTRY,
        summary="Every project, with Status/Stack/Architecture/Team",
        entity_level=2,
        max_bytes=60_000,
        notes="One `## Project` per project; sub-detail at `###`.",
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
        sections=(
            "1. SYSTEM DIRECTIVES & OUTPUT RULES", "2. ATHLETE PROFILE & STATUS",
            "3. ACTIVE PROGRAM & BENCHMARKS", "4. GYM ENVIRONMENT & EQUIPMENT",
            "5. LOG",
        ),
        required=("5. LOG",),
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
    return SPECS.get(path)


def is_retired(path: str) -> bool:
    return path in RETIRED


def owner_of(path: str) -> str | None:
    spec = SPECS.get(path)
    return spec.owner_agent if spec else None


def injected_paths() -> tuple[str, ...]:
    return tuple(p for p, s in SPECS.items() if s.injected)
