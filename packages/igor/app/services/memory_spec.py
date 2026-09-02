# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

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
# v5 extends this to every on-demand document, and the rule that survived is
# WHERE the cut goes, not which kind is allowed to be cut:
#
#   Split by TOPIC first — never INSTEAD of by topic.
#
# `wellness/sessions.md`, `academic/kpss-2026.md`, `finance/ledger` are topics:
# stable, named, and each answering a different question. Cutting a domain
# straight into index keys instead — a folder of months with the repayment
# schedule and the monthly-structure note scattered through them — is the v3
# mistake one storey down (v4 §2.3).
#
# A topic whose index grows without bound may THEN be SHARDED by that key: the
# member stays one topic and becomes a directory, one file per key
# (`finance/ledger/2026-09.md`). The owner asked for this on the ledger, and it
# is his ledger. What it buys is that a month is opened as a month — Sentinel
# reads 700 bytes to answer "what came in in September" instead of every month
# ever recorded. What it costs is that "compare July with August" is two reads
# instead of one. Nothing is cut in half by it: a repayment schedule was never
# in the months, it is `scholarships-and-loans` — which is what splitting by
# topic FIRST is for.
#
# The saving is on the read path. A domain document is opened to answer one
# question about one topic, and the agent paid for all of it: 26 KB of wellness
# to learn today's split, re-sent on every following iteration of the loop.


@dataclass(frozen=True)
class MemberSpec:
    """One named file of a SECTION-split collection (v5).

    A registry splits by entity — one person, one project, one file — and the
    members are open-ended, because the owner meets new people. A domain
    document splits by TOPIC, and its members are a closed, named set: wellness
    has a profile, a program, a gym and a log, and it will have those next year
    too. Naming them here is what makes `wellness/sessions.md` readable instead
    of `wellness/5-log-chronological.md`, and what lets a write to an invented
    member be refused rather than filed.

    How a member is assembled from the source document follows from how many
    sections it takes, with no mode flag to get wrong:

      - ONE source section → the section IS the file. Its heading becomes the
        H1 and everything under it is promoted one level, exactly as a registry
        entity is (`## 5. LOG` → `# Sessions`, `### 2026-08-05` → `## 2026-08-05`).
      - SEVERAL (a literal list, or everything matching `takes_pattern`) → they
        are GATHERED under this member's declared title, unpromoted, staying at
        `##`. This is what keeps `finance/ledger.md` one document: 71 rows across
        `## 2026-07`, `## 2026-08` … in one file, where "compare July with
        August" is still one read. Promoting those would make every month an H1
        and produce a file with twelve titles.
      - SHARDED (`shard=True`, alongside `takes_pattern`) → the member is a
        DIRECTORY and each matched section becomes its own file at
        `<root>/<stem>/<key>.md`, promoted exactly as a single section is: `##
        2026-09` becomes `# 2026-09` at the top of `finance/ledger/2026-09.md`.
        The key is both the filename and the title, which is what lets a write
        route to a month without anything having to remember how a month is
        spelled — and it is why a sharded member declares `index_level=1`.
    """

    stem: str                                   # filename, without .md
    title: str                                  # the member file's H1
    summary: str = ""
    # Which source `##` sections move here. Literal names, or a regex for an
    # index (finance's months). One of the two must be set.
    takes: tuple[str, ...] = ()
    takes_pattern: str | None = None
    # The grammar INSIDE the member, after assembly. Levels are post-promotion:
    # a log that was `### YYYY-MM-DD` under `## 5. LOG` is `## YYYY-MM-DD` here.
    sections: tuple[str, ...] = ()
    required: tuple[str, ...] = ()
    index_pattern: str | None = None
    # How to WRITE a key, for the humans and the models that read tool
    # descriptions: `^\d{4}-\d{2}$` says what a key must be, `YYYY-MM` says what
    # to type. Only the regex is enforced; this is the half that gets it right
    # first time.
    key_shape: str = ""
    index_level: int = 2
    entry_style: str = "bullets"
    entry_sections: tuple[str, ...] = ()
    columns: dict[str, tuple[str, ...]] = field(default_factory=dict)
    markers: tuple[str, ...] = ()
    max_bytes: int = 0                          # 0 = the collection's default
    # This member is a DIRECTORY of one file per index key, not a single file.
    # Only meaningful with `index_pattern` (which says what a key looks like)
    # and `takes_pattern` (which says which source sections become the shards).
    shard: bool = False
    # Cap for ONE shard. `max_bytes` still describes the member as a whole — it
    # is what the pre-shard file was allowed to weigh — but a single month has
    # no business being the size of the whole ledger.
    shard_max_bytes: int = 0                    # 0 = fall back to max_bytes
    notes: str = ""

    @property
    def gathered(self) -> bool:
        """True when this member collects several sections under its own title
        rather than being one promoted section."""
        return self.takes_pattern is not None or len(self.takes) > 1


@dataclass(frozen=True)
class CollectionSpec:
    """A directory whose members all share one grammar.

    Two families live here, and the difference is whether the member set is
    open or closed:

    **Entity collections** (`projects/`, `social/`) split a REGISTRY by entity.
    Members are open-ended and named by slugifying the entity, because a new
    person is a new file and no spec can list him in advance.

    **Section collections** (`wellness/`, `academic/`, …) split a domain
    document by topic. Members are a CLOSED set declared in `members`, so the
    names are chosen rather than slugified, and a write to a member nobody
    declared is refused instead of quietly creating a fourteenth file.

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
    entity_noun: str                            # "project" / "person" / "topic"
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
    # Set for a SECTION collection: the closed, ordered set of member files.
    # Order is the order they are read in — and, for an injected collection, the
    # order they are assembled into the prompt, so it must read like the
    # document it replaces.
    members: tuple[MemberSpec, ...] = ()
    # Injected into every turn's system prompt, assembled from `members` in
    # order. Splitting an injected document saves no tokens — all of it is sent
    # regardless — but it is what lets one agent rewrite `prohibitions` without
    # holding, and risking, the other six sections.
    injected: bool = False

    # A domain GROWS. `academic/` was declared with seven topics read off the
    # document, but a class timetable is academic's and a repayment schedule is
    # finance's, and neither had a member on the day the spec was written.
    # Freezing the set would send both to `life/`, which is how a last-resort
    # folder becomes the place everything actually lives — the junk drawer that
    # this whole architecture exists to abolish.
    #
    # So the OWNING agent may add a topic to its own folder. Nobody else may:
    # `owner_agent` is still enforced, so Ultron extends `academic/` and cannot
    # touch `finance/`. The declared members keep their exact grammar; a new one
    # gets the collection's default. Extensibility is what makes "file it in the
    # domain that owns the subject" an instruction an agent can actually follow.
    extensible: bool = True

    @property
    def closed(self) -> bool:
        """Declares a fixed member set — its grammar is per-member."""
        return bool(self.members)

    def member(self, stem: str) -> MemberSpec | None:
        return next((m for m in self.members if m.stem == stem), None)


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
        # TWO categories, by the owner's decision. "Siberay Board" was a third,
        # and it was a category mistake: a board seat is a professional
        # relationship, not a kind of person, and every member of it was already
        # a colleague. A taxonomy that grows a folder per organisation ends with
        # one person filed in three places and no answer to "who is she".
        # Everyone is professional or personal; the organisation is a fact
        # RECORDED on the person, not the folder they live in.
        groups=("professional", "personal"),
        # People carry `**Who:**` / `**Events:**` as bold markers, not headings,
        # so there is nothing to put in `sections` — the check is on the markers.
        markers=("**Who:**", "**Events:**"),
        max_bytes=8_000,
        split_from="/memories/social.md",
        entity_level=3,
        notes="**Who:** paragraph, then **Events:** dated bullets, newest first.",
    ),
)

SNAPSHOT = "snapshot"   # the current version of a recurring artifact

SECTION_COLLECTIONS: tuple[CollectionSpec, ...] = (
    CollectionSpec(
        # OPEN, on purpose — the one folder where an agent may create a file the
        # spec never heard of.
        #
        # Everything else in the store is a domain with a known shape, and
        # closing those is what stops a fourteenth wellness topic appearing
        # under a name only one agent knows. But the owner sends things that are
        # none of those domains and are genuinely new: the dorm's monthly dinner
        # menu, a class timetable, a rental contract's terms. They have no home
        # in wellness or academic, and refusing them means the fact is simply
        # lost — which is worse than an imperfect filing.
        #
        # These are SNAPSHOTS, and that is the whole point of the kind: such a
        # document gets REISSUED, and the new edition replaces the old rather
        # than appending to it. A file that accumulates editions cannot answer
        # the question it exists to answer — there would be twelve answers and
        # no way to tell which is current. Nothing is lost: every write records
        # its `before` in the revision trail, so the prior edition is one
        # restore away.
        root="/memories/life",
        kind=SNAPSHOT,
        summary="Recurring documents the owner sends — current version only",
        entity_noun="document",
        depth=1,
        max_bytes=12_000,
        notes=(
            "One file per recurring artifact, named for the thing itself "
            "(`dorm-menu.md`, `class-timetable.md`). A new version REPLACES the "
            "file wholly; the previous one stays in the revision trail. Put the "
            "period it covers in the H1 so a stale file is obvious on sight."
        ),
    ),
    CollectionSpec(
        root="/memories/wellness",
        kind=LEDGER,
        summary="Training — directives, profile, program, gym, session log",
        entity_noun="topic",
        owner_agent="atomix",
        split_from="/memories/wellness.md",
        entity_level=2,
        members=(
            MemberSpec(
                stem="directives", title="System Directives & Output Rules",
                takes=("1. SYSTEM DIRECTIVES & OUTPUT RULES",),
                summary="How Atomix reports training",
            ),
            MemberSpec(
                stem="profile", title="Athlete Profile & Status",
                takes=("2. ATHLETE PROFILE & STATUS",),
                summary="Body metrics, injuries, current condition",
            ),
            MemberSpec(
                stem="program", title="Active Program & Benchmarks",
                takes=("3. ACTIVE PROGRAM & BENCHMARKS",),
                summary="The split he is running and his current numbers",
            ),
            MemberSpec(
                stem="gym", title="Gym Environment & Equipment",
                takes=("4. GYM ENVIRONMENT & EQUIPMENT",),
                summary="What his gym actually has",
            ),
            MemberSpec(
                # The one that grows without bound, and the reason this document
                # was 26 KB: every session ever, re-read in full to answer "what
                # did I lift on Tuesday".
                stem="sessions", title="Session Log",
                takes=("5. LOG (Chronological)",),
                summary="Every training session, newest first",
                index_pattern=r"^\d{4}-\d{2}-\d{2}",
                index_level=2,
                entry_style="bullets",
                max_bytes=48_000,
                notes="`## YYYY-MM-DD — <split> · <status>`, newest first.",
            ),
        ),
    ),
    CollectionSpec(
        root="/memories/academic",
        kind=LEDGER,
        summary="University — calendar, KPSS, materials, Erasmus, standing",
        entity_noun="topic",
        owner_agent="ultron",
        split_from="/memories/academic.md",
        entity_level=2,
        members=(
            MemberSpec(stem="kpss-2026", title="KPSS 2026", takes=("KPSS 2026",),
                       summary="Exam preparation — subjects, topics, net averages"),
            MemberSpec(stem="akademik-takvim", title="Akademik Takvim",
                       takes=("Akademik Takvim",),
                       summary="Term dates, exam weeks, registration deadlines"),
            MemberSpec(stem="ders-materyalleri", title="Ders Materyalleri",
                       takes=("Ders Materyalleri",),
                       summary="Course books, slides, assignment specs"),
            MemberSpec(stem="erasmus", title="Erasmus+", takes=("Erasmus+",),
                       summary="Application, evaluation, placement"),
            MemberSpec(
                # Read off the real document, not guessed: the owner's whole
                # 2022- YBS curriculum, as the official course tables per term.
                stem="mufredat", title="Müfredat (2022-günümüz)",
                takes=("Müfredat (2022-günümüz)",),
                summary="The YBS curriculum he is bound to — course tables per term",
                notes="`## <sınıf> — <dönem>` per term, each a course table.",
            ),
            MemberSpec(stem="akademik-durum", title="Akademik Durum",
                       takes=("Akademik Durum",),
                       summary="Standing — GNO, exemptions, applications",
                       notes="Dated bullets, `[YYYY-MM-DD, agent]`, newest last."),
            MemberSpec(stem="session-durumu", title="Session Durumu",
                       takes=("Session Durumu",),
                       summary="Which course-material sessions are built"),
        ),
    ),
    CollectionSpec(
        root="/memories/finance",
        kind=LEDGER,
        summary="Money — the monthly ledger, plus standing reference",
        entity_noun="topic",
        owner_agent="sentinel",
        split_from="/memories/finance.md",
        entity_level=2,
        members=(
            MemberSpec(
                # SHARDED by month, on the owner's instruction: the member is a
                # DIRECTORY and every `## YYYY-MM` is its own file. It is still
                # ONE topic — the months are the same document, indexed — so the
                # split-by-topic rule above holds; what moved is where the index
                # lives. It is on the filesystem now, which means a month costs
                # a month to read instead of costing the whole ledger, and a
                # cross-month comparison costs one read per month.
                stem="ledger", title="Aylık Defter",
                takes_pattern=r"^\d{4}-\d{2}$",
                shard=True,
                summary="Every month — incomes, expenses, debts (one file per month)",
                index_pattern=r"^\d{4}-\d{2}$",
                key_shape="YYYY-MM",
                # In a shard the KEY is the H1, so everything the month carries
                # sits one level above where it sat inside the gathered file:
                # `### Incomes` under `## 2026-09` becomes `## Incomes` under
                # `# 2026-09`.
                index_level=1,
                entry_style="table",
                entry_sections=("Incomes", "Expenses", "Debts"),
                columns={
                    "Incomes": ("Date", "Source", "Amount (TL)", "Notes"),
                    "Expenses": ("Date", "Item", "Amount (TL)", "Notes"),
                    "Debts": ("Debt", "Amount (TL)", "Status", "Notes"),
                },
                max_bytes=48_000,
                shard_max_bytes=12_000,
            ),
            MemberSpec(stem="monthly-structure", title="Monthly Structure",
                       takes=("Monthly structure",),
                       summary="How a month is expected to look"),
            MemberSpec(stem="notes", title="Notes", takes=("Notes",),
                       summary="Standing notes on the ledger's conventions"),
            MemberSpec(stem="scholarships-and-loans", title="Scholarships & Loans",
                       takes=("Scholarships & Loans (Reference)",),
                       summary="KYK and scholarship terms, repayment schedules"),
            MemberSpec(stem="blackwalnut", title="BLACKWALNUT — Continuous Wealth Strategies",
                       takes=("BLACKWALNUT — Continuous Wealth Strategies",),
                       summary="Long-run wealth strategy"),
        ),
    ),
    CollectionSpec(
        root="/memories/ops",
        kind=LEDGER,
        summary="The host — runbook and action log",
        entity_noun="topic",
        owner_agent="orion",
        split_from="/memories/ops.md",
        entity_level=2,
        members=(
            MemberSpec(
                stem="runbook", title="Service Map & Procedures",
                takes=("Part 1: Self-Guide — Service Map & Procedures",),
                summary="What runs where, and how to operate it",
                notes=(
                    "Operational facts go stale silently and are then acted on. "
                    "Every claim about the running system carries the date it "
                    "was verified."
                ),
            ),
            MemberSpec(
                stem="actions", title="Action Log",
                takes=("Part 2: Action Log",),
                summary="What Orion did to the host, dated",
                index_pattern=r"^\d{4}-\d{2}-\d{2}",
                index_level=2,
                entry_style="bullets",
                max_bytes=24_000,
            ),
        ),
    ),
    CollectionSpec(
        root="/memories/cybersec",
        kind=LEDGER,
        summary="Security learning — tracks, progress, resources",
        entity_noun="topic",
        owner_agent="centurion",
        split_from="/memories/cybersec.md",
        entity_level=2,
        members=(
            MemberSpec(stem="structure", title="Structure", takes=("Structure",),
                       summary="How the learning path is organised"),
            MemberSpec(
                # The document calls this "Curriculum", not "Tracks" — the
                # earlier spec was written from a taxonomy note rather than from
                # the file, which is the exact failure memory_spec's header
                # warns about: a spec that disagrees with its document turns the
                # verifier into noise.
                stem="curriculum", title="Curriculum", takes=("Curriculum",),
                summary="The seven modules and where he is in them",
            ),
            MemberSpec(stem="progress-log", title="Progress Log", takes=("Progress Log",),
                       summary="Dated progress"),
            MemberSpec(stem="resources", title="Resources", takes=("Resources",),
                       summary="Courses, labs, reading"),
            MemberSpec(stem="certifications", title="Certifications / Goals",
                       takes=("Certifications / Goals",),
                       summary="Targets and dates"),
        ),
    ),
    CollectionSpec(
        root="/memories/dossier",
        kind=OBSERVATIONS,
        summary="How to treat him — preferences, prohibitions, trusted contacts",
        entity_noun="topic",
        # INJECTED, and split anyway. There is no token saving here — every
        # member is sent on every turn regardless — but there is a correctness
        # one: `prohibitions` is a binding behavioural contract and `likes` is a
        # mild preference, and while they shared a file every write to either
        # held both. v3 collapsed all six into one heading called "General" and
        # a hard prohibition became indistinguishable from a preference.
        injected=True,
        split_from="/memories/dossier.md",
        entity_level=2,
        members=(
            MemberSpec(stem="likes", title="Likes / responds well to",
                       takes=("Likes / responds well to",), summary="What lands well"),
            MemberSpec(stem="dislikes", title="Dislikes / friction",
                       takes=("Dislikes / friction",), summary="What causes friction"),
            MemberSpec(stem="wants", title="Wants — and in what manner",
                       takes=("Wants — and in what manner",),
                       summary="What he wants, and how he wants it delivered"),
            MemberSpec(stem="brainstorm-mode", title="Brainstorm Mode",
                       takes=("Brainstorm Mode",),
                       summary="How to behave when he is thinking out loud"),
            MemberSpec(stem="prohibitions", title="Explicit prohibitions",
                       takes=("Explicit prohibitions",),
                       summary="BINDING — violate one and the turn has failed"),
            MemberSpec(stem="contacts", title="Trusted contacts & services",
                       takes=("Trusted contacts & services",),
                       summary="Who and what he trusts"),
            # No `open-questions` member: the real dossier.md has six sections,
            # not seven. The old DocumentSpec listed a seventh that does not
            # exist, and declaring a member for it would have written an empty
            # file on every migration and then failed its own required check.
        ),
    ),
)

COLLECTIONS = COLLECTIONS + SECTION_COLLECTIONS

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
    """The collection a member path belongs to, or None.

    A CLOSED collection resolves only its declared members: `wellness/notes.md`
    is not a wellness file that happens to be undeclared, it is a file nobody
    designed, and returning None here is what makes the write gate refuse it
    instead of creating a fourteenth topic under a name only one agent knows.
    """
    for coll in COLLECTIONS:
        prefix = coll.root + "/"
        if not path.startswith(prefix) or not path.endswith(".md"):
            continue
        rest = path[len(prefix):]
        if rest.count("/") == coll.depth and coll.depth == 1:
            # A SHARDED member sits one level deeper than its siblings, because
            # it is a folder rather than a file: `finance/ledger/2026-09.md`
            # beside `finance/notes.md`.
            stem = rest.split("/", 1)[0]
            m = coll.member(stem)
            if m is not None and m.shard:
                return coll
            continue
        if rest.count("/") != coll.depth - 1:
            continue
        if coll.closed and not coll.extensible:
            return coll if coll.member(rest[:-3]) else None
        if coll.depth == 2 and coll.groups:
            group = rest.split("/", 1)[0]
            if group not in coll.groups:
                return None
        return coll
    return None


def shard_member(path: str) -> tuple[CollectionSpec, MemberSpec] | None:
    """The collection and member for ONE shard file — `finance/ledger/2026-09.md`.

    Does not check the key against the member's `index_pattern`: a badly named
    shard still has to resolve to a spec, or the verifier could not say what is
    wrong with it.
    """
    for coll in COLLECTIONS:
        prefix = coll.root + "/"
        if coll.depth != 1 or not path.startswith(prefix) or not path.endswith(".md"):
            continue
        rest = path[len(prefix):]
        if rest.count("/") != 1:
            continue
        member = coll.member(rest.split("/", 1)[0])
        if member is not None and member.shard:
            return coll, member
    return None


def shard_root(path: str) -> tuple[CollectionSpec, MemberSpec] | None:
    """The collection and member for a sharded member's DIRECTORY.

    Accepts it with or without `.md`, deliberately: `finance/ledger.md` is what
    the member was before it became a folder, and every agent that learned the
    store before the shard names it that way. Resolving both is what turns that
    into a redirect instead of a refusal.
    """
    p = path.rstrip("/").removesuffix(".md")
    for coll in COLLECTIONS:
        prefix = coll.root + "/"
        if coll.depth != 1 or not p.startswith(prefix):
            continue
        stem = p[len(prefix):]
        if "/" in stem:
            continue
        member = coll.member(stem)
        if member is not None and member.shard:
            return coll, member
    return None


def shard_path(coll: CollectionSpec, member: MemberSpec, key: str) -> str:
    """Where one key's file lives. The one function that decides this."""
    return f"{coll.root}/{member.stem}/{key}.md"


def member_for(path: str) -> tuple[CollectionSpec, MemberSpec] | None:
    """The collection and the declared member for a section-collection path."""
    coll = collection_for(path)
    if coll is None or not coll.closed:
        return None
    member = coll.member(path.rsplit("/", 1)[-1][:-3])
    return (coll, member) if member else None


def member_for_key(coll: CollectionSpec, key: str) -> MemberSpec | None:
    """Which member a dated/periodic key belongs to.

    This is how a write survives the split without the agent having to relearn
    the store: `ledger_append("wellness", "2026-08-05", …)` names a domain and a
    date, and the date itself says which file — only `sessions` indexes
    `YYYY-MM-DD`. Deterministic, and it fails closed: a key no member indexes
    resolves to nothing rather than to the first file in the folder.
    """
    import re as _re

    for m in coll.members:
        if m.index_pattern and _re.match(m.index_pattern, key):
            return m
    return None


def member_path(coll: CollectionSpec, title: str, group: str | None = None) -> str:
    """Where an entity's file belongs. The one function that decides this, so a
    write and a read can never disagree about where a person lives."""
    if coll.closed:
        # A closed collection is addressed by its declared names, not by
        # slugifying whatever the caller typed — that is the difference between
        # "the log" resolving to sessions.md and it creating `the-log.md`.
        want = slugify(title)
        for m in coll.members:
            if want in (m.stem, slugify(m.title)):
                if m.shard:
                    # A sharded member has no single file to name — asking for
                    # one is a caller that does not know it is a folder, and
                    # handing back `finance/ledger.md` would be handing back a
                    # path that no longer exists.
                    raise ValueError(
                        f"{m.stem!r} is sharded by index key — it is a directory. "
                        f"Route by the key (`route_ledger`), not by the name."
                    )
                return f"{coll.root}/{m.stem}.md"
        raise ValueError(
            f"{title!r} is not a topic of {coll.root} "
            f"({', '.join(m.stem for m in coll.members)})"
        )
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
    shard = shard_member(path)
    if shard is not None:
        # One key of a sharded member. It carries the member's grammar — same
        # tables, same columns, same owner — with the key as its H1 and its own
        # cap, because the member's cap describes the whole index and this is
        # one month of it.
        _, member = shard
        key = path.rsplit("/", 1)[-1][:-3]
        return DocumentSpec(
            path=path,
            kind=coll.kind,
            summary=f"{member.title} — {key}",
            owner_agent=coll.owner_agent,
            injected=False,
            sections=member.sections,
            required=member.required,
            entity_level=None,
            index_pattern=member.index_pattern,
            index_level=member.index_level,
            entry_style=member.entry_style,
            entry_sections=member.entry_sections,
            columns=member.columns,
            max_bytes=member.shard_max_bytes or member.max_bytes or coll.max_bytes,
            notes=member.notes or coll.notes,
        )

    member = coll.member(path.rsplit("/", 1)[-1][:-3]) if coll.closed else None
    if member is not None:
        # A declared topic carries its own grammar — the log is a dated index,
        # the ledger is monthly tables, the profile is prose. Handing each its
        # real shape is what lets `check_write` reject a table row appended to
        # the gym inventory without a single line of code knowing that wellness
        # was ever one file.
        return DocumentSpec(
            path=path,
            kind=coll.kind,
            summary=member.summary or member.title,
            owner_agent=coll.owner_agent,
            injected=coll.injected,
            sections=member.sections,
            required=member.required,
            entity_level=None,
            index_pattern=member.index_pattern,
            index_level=member.index_level,
            entry_style=member.entry_style,
            entry_sections=member.entry_sections,
            columns=member.columns,
            max_bytes=member.max_bytes or coll.max_bytes,
            notes=member.notes or coll.notes,
        )
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
            superseded_by="/memories/dossier",
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
    "/memories/patterns.md": DocumentSpec(
        path="/memories/patterns.md",
        kind=OBSERVATIONS,
        summary="Induced behavioural patterns, each with the move that pre-empts it",
        # INJECTED, and that is the whole point. A pattern that has to be
        # searched for is a diary entry: by the time anyone thinks to look it up,
        # the owner has already walked into the thing it predicted. The value of
        # this document is entirely in being in front of every agent BEFORE the
        # turn starts, which is why it is small, capped hard, and carries the
        # countermeasure on the same line as the observation.
        #
        # Deliberately NOT a dossier member. The dossier records what he likes,
        # dislikes and forbids — claims that are true because he said so. A
        # pattern is INDUCED from several facts, carries a confidence, and can
        # simply be wrong. Filing a fallible inference beside a binding
        # prohibition is how the two stop being told apart, which is the exact
        # defect v4 §2.2 records one storey down.
        injected=True,
        # 12K, not the 8K this file wants, because `memory_schema.max_bytes_for`
        # resolves an injected path from INJECTED_FILE_MAX_BYTES and never reads
        # the cap declared here — the same reason current.md's declared 6K is not
        # the 12K actually enforced on it. Declaring a number that is not the one
        # applied is how a spec starts lying, so this matches reality; the prompt
        # carries the "stay short" half.
        max_bytes=12_000,
        sections=("Behaviour", "Tendencies", "Correlations"),
        # The vocabulary is `observations.PATTERN_TYPES` minus the two the
        # dossier already owns (`preference`, `personality`). Reusing the record's
        # words rather than inventing prose headings is what lets an inductive
        # observation and its line here be recognised as the same claim.
        notes=(
            "Every line is `- [YYYY-MM-DD, agent_id, confidence] pattern → the move`. "
            "The arrow is required: a pattern with no countermeasure is an "
            "observation, and observations belong in the record, not here."
        ),
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
            superseded_by="/memories/finance",
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
            superseded_by="/memories/wellness",
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
            superseded_by="/memories/academic",
    ),
    "/memories/cybersec.md": DocumentSpec(
        path="/memories/cybersec.md",
        kind=LEDGER,
        summary="Cybersecurity learning journey",
        owner_agent="centurion",
        sections=("Structure", "Tracks", "Progress Log", "Resources",
                  "Certifications / Goals"),
        max_bytes=24_000,
            superseded_by="/memories/cybersec",
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
            superseded_by="/memories/ops",
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


def normalize(name: str) -> str:
    """What the agent typed → a `/memories/...` path.

    Agents name documents the way people do — `wellness`, `wellness.md`,
    `/memories/wellness.md` — and every one of those is the same request. This
    is the one place that decides, so a write and a read cannot disagree.
    """
    p = (name or "").strip()
    if not p:
        return ""
    if not p.startswith("/memories/"):
        p = "/memories/" + p.lstrip("/")
    if not p.endswith(".md") and not is_collection_root(p) and not shard_root(p):
        p += ".md"
    return p


def route_ledger(path: str, key: str) -> tuple[str, str]:
    """The member file a dated entry belongs in — (path, error).

    An agent that learned the store before the split names the domain
    (`wellness.md`) and the date; a folder has no single place to put that, but
    the DATE does: only `sessions` indexes `YYYY-MM-DD`, only `ledger` indexes
    `YYYY-MM`. Routing on the key means the old call keeps working and lands in
    the right file, instead of being refused for naming a document that is now
    a directory.

    Returns the path unchanged when it is already a member or an unsplit
    document. Never guesses: a key no member indexes comes back as an error
    naming the members, because filing August's rent under `gym` would be worse
    than not filing it.
    """
    import re as _re

    p = normalize(path)

    # A sharded member is a folder of keys, so the KEY is the file. This catches
    # every way the caller can name it: the folder, one month directly, or
    # `finance/ledger.md` — the file it was before the shard, which is what an
    # agent that learned the store earlier will say.
    shard = shard_member(p) or shard_root(p)
    if shard is not None:
        coll, member = shard
        if member.index_pattern and not _re.match(member.index_pattern, key):
            return "", (
                f"`{key}` is not an index key of {coll.root}/{member.stem} — a key "
                f"there must match {member.index_pattern}. Nothing was written."
            )
        return shard_path(coll, member, key), ""

    coll = collection_by_root(p.removesuffix(".md")) or collection_by_root(p)
    if coll is None:
        spec = SPECS.get(p)
        if spec is not None and spec.superseded_by:
            coll = collection_by_root(spec.superseded_by)
    if coll is None or not coll.closed:
        return p, ""

    member = member_for_key(coll, key)
    if member is None:
        indexed = [f"{m.stem} ({m.index_pattern})" for m in coll.members if m.index_pattern]
        return "", (
            f"`{key}` is not an index key of anything under {coll.root}. "
            + (f"Dated members are: {', '.join(indexed)}. "
               if indexed else f"Nothing under {coll.root} is a dated ledger. ")
            + "Name the member file directly if this is not a dated entry — "
              f"{', '.join(m.stem for m in coll.members)}."
        )
    if member.shard:
        return shard_path(coll, member, key), ""
    return f"{coll.root}/{member.stem}.md", ""


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


def injected_paths(existing: set[str] | None = None) -> tuple[str, ...]:
    """Every path assembled into the system prompt, in reading order.

    A split injected document contributes its members instead of itself, in the
    order the collection declares — that order is what the owner reads, so it
    has to match the document it replaces rather than the filesystem's idea of
    alphabetical. The monolith is deliberately absent once its members exist:
    injecting both would send the same seven sections twice, one of them stale.

    `existing` is the set of paths that actually exist, and passing it is what
    makes this correct on BOTH sides of the migration. This code deploys via
    GitOps and therefore lands on the server BEFORE anyone runs the split;
    without the check, the turn after deploy would inject seven dossier members
    that do not exist yet and silently drop the document that governs how every
    agent behaves. Omit it only where the answer is a static question ("is this
    path injected") rather than an assembly.
    """
    out: list[str] = []
    for path, spec in SPECS.items():
        if not spec.injected:
            continue
        coll = collection_by_root(spec.superseded_by) if spec.superseded_by else None
        if coll is None or not coll.injected:
            out.append(path)
            continue
        members = [f"{coll.root}/{m.stem}.md" for m in coll.members]
        if existing is not None and not any(p in existing for p in members):
            out.append(path)          # not split yet — the monolith is still it
        else:
            out.extend(members)
    return tuple(out)


def collection_summaries() -> dict[str, str]:
    """Collection roots for the canonical taxonomy and the systems board."""
    return {c.root: c.summary for c in COLLECTIONS}
