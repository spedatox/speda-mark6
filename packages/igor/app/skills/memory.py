# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Virtual owner-memory filesystem and bounded recall context.

The `memory` tool exposes reads to agents. Durable sourced facts are recorded
through `record_observation`; managed documents have evidence-bound domain
writers. Old raw write handlers remain below for compatibility but are not
advertised or reachable through `MemorySkill.execute`.
"""

import logging
import re
from datetime import datetime, time, timezone

from sqlalchemy import select, delete as sql_delete

from app.core.context import AgentContext
from app.models.memory_file import MemoryFile
from app.services.memory_schema import MemorySchemaViolation, check_write
from app.services.memory_store import record_revision, mutate_file, MemoryWriteConflict
from app.skills.base import Skill

logger = logging.getLogger(__name__)

MEMORY_ROOT = "/memories"

# ── Initial file templates seeded on first use ────────────────────────────────

INITIAL_FILES = {
    "/memories/owner.md": """\
# Owner Profile — who he is, and what shaped him before Mark VI

**Name:** Ahmet Erol Bayrak
**Codename:** Spedatox
**How to address him:** Ahmet Erol — by name, sparingly. No honorifics, ever.

_Identity constants above. Below: his biography up to the creation of Mark VI
(2026-05) — the fixed prior that lets an agent know the man it serves. Updated in
place as facts are revealed or corrected; the past does not expire. Behavioural
preferences do NOT belong here — those live in dossier.md._

## Biography (pre-Mark VI)
(education, places, formative work, family background — the events that explain
him. Organised by theme or era, not as a diary.)
""",
    "/memories/current.md": """\
# Current — what's active right now

<!-- state-projection-v1 -->

Computed from /memories/states/. Use memory_state for ongoing situations and
confirmed plans; completed events belong in the subject's dated log.
""",
    "/memories/dossier.md": """\
# Dossier — what we've observed about how he wants to be treated

_The agents' working model of the owner's preferences, built as they talk to him:
what he likes, dislikes, and wants — and in what manner. Owner-stated preferences
only; inferred patterns belong separately in patterns.md. Every entry is attributed and dated: `- [YYYY-MM-DD,
agent_id] observation`. Agents LEARN from this and act on it silently; it is never
read aloud or cited to him._

## Likes / responds well to

## Dislikes / friction

## Wants — and in what manner
(task-shaped standing observations, e.g. "wants plans as numbered concrete steps,
not prose")

## Open questions
(things still unclear about the owner)
""",
    "/memories/patterns.md": """\n# Patterns — owner-facing ACE view

_Operational pattern state is held by ACE around canonical inductive
observations. This markdown file is an inspectable compatibility surface, not a
second source of truth and not something agents manually maintain during a
turn. Use `inspect_patterns` for live evidence, confidence and countermeasures._

## Behaviour
(what he repeatedly DOES, in situations that recur)

## Tendencies
(how he repeatedly leans — pace, ambition, follow-through, what he defers)

## Correlations
(when X, then Y — the conditional ones: a state that predicts an outcome)
""",
    # projects.md and social.md are deliberately absent: they are REGISTRIES and
    # each is now one file per entity under /memories/projects/ and
    # /memories/social/<category>/ (memory_spec.COLLECTIONS). A collection has
    # nothing to seed — an owner with no projects yet correctly has no project
    # files, and the first one is a `create` on its own path.
    #
    # Leaving the seeds here would be actively harmful, and the file already
    # records why one storey down: a seed is what made `ensure_seeded` recreate
    # sessions.md on the next turn after every deletion. Once the split has run
    # and the monoliths are deleted, a seed would resurrect them empty, and an
    # empty projects.md next to a populated projects/ folder is exactly the
    # "facts drift between files" failure the taxonomy exists to prevent.
    # sessions.md is retired — wellness.md is the same document continued. It is
    # deliberately absent here: leaving it in meant `ensure_seeded` recreated it
    # on the next turn after any deletion, which is why removing a file has to
    # start with removing its seed.
    "/memories/wellness.md": """\
# Wellness — training protocol and log

_Atomix is the only writer; other agents read. Program-level life context
("cutting for the wedding") belongs in current.md, not here._

## 1. SYSTEM DIRECTIVES & OUTPUT RULES

_How programs are created, logged and delivered. Read first._

## 2. ATHLETE PROFILE & STATUS

_Strengths, weak points, injuries and limitations. Updated in place._

## 3. ACTIVE PROGRAM & BENCHMARKS

_Current split and working loads per main lift, dated._

## 4. GYM ENVIRONMENT & EQUIPMENT

_What the gym actually has, and what is missing, broken or always occupied.
Atomix asks; never assumes._

## 5. LOG

_One entry per session, newest first:_

<!-- Schema — copy per session:
### YYYY-MM-DD — <split> · <status>
- <exercise> <sets> × <reps> @ <load> — <note>
- **Note:** <deviations, pain, skipped sets, substitutions, energy>
-->
""",
    "/memories/log.md": """\
# Session Log

(Rolling dated summary of recent sessions — most recent first)
""",
    "/memories/finance.md": """\
# Finance — the owner's financial source of truth

_Sentinel's domain file. The authoritative record of the owner's finances:
accounts, balances, income, recurring expenses, budgets, holdings and financial
goals. Sentinel READS this for every figure it reports and WRITES every update
here (via the memory tool). Keep it current — supersede stale figures in place,
date material changes. Program-level life context ("saving for the wedding")
belongs in current.md with a cross-reference._

## Accounts & balances

## Income

## Recurring expenses

## Budgets & goals

## Holdings / investments
""",
    "/memories/history.md": """\
# History — the Mark VI era ledger

_Things that began AND ended during Mark VI's watch (since 2026-05) and no longer
apply. Populated only by demotion from current.md / projects.md / social.md, each
entry carrying its active date range. Pre-Mark-VI context does NOT belong here —
that is owner.md. Organised by theme:_

## Employment

## Completed / Retired Projects

## Past States

## People
""",
}

# Files preloaded into the system prompt every turn — the "always relevant" set:
# who the owner is, what's current, how to treat him, and the immutable past that
# stops stale facts masquerading as current ones.
#
# Derived from the specs rather than listed here, because a split injected
# document contributes its MEMBERS instead of itself: dossier.md became
# dossier/likes.md … dossier/prohibitions.md, and the injected block has to
# carry the same seven sections it always did, in the same order. Hardcoding
# the old path here would have injected an empty husk (or, worse, a stale
# monolith next to its live members) the day the split ran.
#
# `injected_paths(existing)` falls back to the monolith for as long as it is
# unsplit, so this is correct on both sides of the migration — and during it.
def preload_paths(existing: set[str] | None = None) -> list[str]:
    from app.services.memory_spec import injected_paths

    return list(injected_paths(existing))


# The static view, for callers that only ask "is this path injected".
PRELOAD_FILES = preload_paths()

# Per-agent SOURCE-OF-TRUTH file: the one domain file an agent both reads (it is
# preloaded into the system prompt every turn) and writes (all of its domain data
# goes there). These are the built-in defaults; the owner can reassign any agent's
# source file from the desktop Configuration tab (runtime_state.get_agent_sources,
# which overrides this map per agent). Atomix owns the gym log, Sentinel the
# finance ledger (docs/MEMORY_ARCHITECTURE.md §2.1).
# Which agent owns which document. Not a preference — the revision trail shows
# this is already how the store behaves (academic.md written by ultron 19 times
# and nobody else, wellness.md by atomix 11 of 12, finance.md by sentinel 23 of
# 32). Recording it makes it enforceable instead of merely true so far.
#
# atomix moved from sessions.md to wellness.md: they are the same document
# continued — identical section structure, wellness.md newer and larger — and
# sessions.md is retired (memory_store.RETIRED_FILES).
AGENT_SOURCE_DEFAULTS: dict[str, str] = {
    "atomix": "/memories/wellness",
    "sentinel": "/memories/finance",
    "ultron": "/memories/academic",
    "scourge": "/memories/cybersec",
    "orion": "/memories/ops",
}


def source_file_for(agent_id: str) -> str | None:
    """The agent's active source of truth — now a DIRECTORY, not a file.

    The owner's runtime override wins (he can still pin a single file from the
    Configuration tab), then the built-in default, then None.
    """
    from app.core.runtime_state import get_agent_sources

    return get_agent_sources().get(agent_id) or AGENT_SOURCE_DEFAULTS.get(agent_id)


# ── Path validation ───────────────────────────────────────────────────────────

def _validate_path(path: str) -> str | None:
    """Return error string if path is invalid, None if OK."""
    if path != MEMORY_ROOT and not path.startswith(MEMORY_ROOT + "/"):
        return f"Error: Path must start with {MEMORY_ROOT}. Got: {path}"
    if ".." in path or "\\" in path or "//" in path:
        return f"Error: Path traversal not allowed: {path}"
    return None


# ── Formatting helpers ────────────────────────────────────────────────────────

def _schema_note(
    *, path: str, before: str, after: str, is_create: bool, agent_id: str
) -> str:
    """Run the pending write past the file law (app/services/memory_schema.py).

    Raises MemorySchemaViolation when the write would newly break it — the caller
    returns that message verbatim as the tool result and saves nothing. Otherwise
    returns advisory warnings to append to a successful result, so the agent
    learns the rule on the write that nearly broke it rather than a day later in
    Orion's audit log.
    """
    warnings = check_write(
        path=path, before=before, after=after, is_create=is_create, author=agent_id
    )
    if not warnings:
        return ""
    return "\n\nNote:\n  - " + "\n  - ".join(warnings)


def _format_file_with_lines(path: str, content: str) -> str:
    lines = content.splitlines()
    numbered = "\n".join(f"{i + 1:6}\t{line}" for i, line in enumerate(lines))
    return f"Here's the content of {path} with line numbers:\n{numbered}"


def _format_directory(
    files: list[MemoryFile],
    path: str,
    *,
    with_sizes: bool = True,
    collapse: int = 0,
) -> str:
    """Render a file listing, grouped by folder.

    Grouping is not cosmetic. Since projects and people became one file each
    (memory_spec.COLLECTIONS) this listing carries ~40 paths instead of ~14, and
    it is injected into EVERY turn for EVERY agent — repeating
    `/memories/social/professional/` on each line would spend a few hundred
    tokens per request restating a prefix. It is also what makes the listing an
    index the model can actually use: every project and person is visible by
    name, so the right file can be opened directly instead of searched for.

    `collapse` caps that generosity: past that many files a folder is rendered
    as a name and a count instead of a list. See the branch below for why the
    trade changed once the observation store could answer entity questions
    without a file being opened. 0 enumerates everything, as before.

    Output is a pure function of the sorted path set, which is what keeps the
    injected block byte-stable and therefore cacheable.
    """
    if not files:
        return f"Here are the files and directories up to 2 levels deep in {path}:\n(empty)"

    def _size(f: MemoryFile) -> str:
        n = len(f.content.encode("utf-8"))
        return f"{n / 1024:.1f}K\t" if n >= 1024 else f"{n}B\t"

    # Size-free listing — stable across turns so the recall block stays cacheable
    # (file sizes change every turn as log.md grows, which would otherwise bust
    # the prompt cache on every request).
    def _label(f: MemoryFile, name: str) -> str:
        return (_size(f) if with_sizes else "") + name

    groups: dict[str, list[MemoryFile]] = {}
    for f in sorted(files, key=lambda x: x.path):
        parent = f.path.rsplit("/", 1)[0]
        # Dot-directories are the store's back office — `.archive/` holds the
        # monoliths their members replaced, `.audit/` holds Orion's trail. Both
        # are readable by path if anyone needs them, but listing them offers
        # every agent a 27 KB superseded copy of a document it should be reading
        # as a 4 KB member, and the listing is precisely where an agent decides
        # what to open. A back office nobody advertises is not a hidden one.
        if parent.rsplit("/", 1)[-1].startswith("."):
            continue
        groups.setdefault(parent, []).append(f)

    # A collection with no files yet still has to appear, or it does not exist as
    # far as the agent is concerned. `general/` is empty until the first event
    # or reference that belongs to no domain arrives — and the prompt tells
    # agents to file such a document there, while this listing is what they
    # trust for what exists. The two disagreeing is how an instruction quietly
    # stops being followed.
    from app.services.memory_spec import COLLECTIONS

    for coll in COLLECTIONS:
        if coll.depth == 1 and not coll.closed:
            groups.setdefault(coll.root, [])

    lines = [f"Here are the files and directories up to 2 levels deep in {path}:"]
    root = path.rstrip("/") or MEMORY_ROOT
    for parent in sorted(groups):
        members = groups[parent]
        if not members:
            lines.append(f"{parent}/")
            lines.append("  (empty)")
            continue
        if parent == root:
            # Top level keeps full paths: these are the documents agents are told
            # about by name in prompts, and a bare `owner.md` reads like a
            # relative path the tool would then reject.
            lines.extend(_label(f, f.path) for f in members)
        elif collapse and len(members) > collapse:
            # A folder past the threshold is announced by NAME AND COUNT rather
            # than enumerated. /memories/projects alone is 33 entries, and this
            # listing is re-sent on every turn of every session. Spending
            # hundreds of tokens per request on a table of contents made sense
            # while it was the only way to find an entity, and stopped making
            # sense once `search_memory` could answer a question about a person
            # or a project without their file being opened at all. The names are
            # one `view` away for the rarer case where the narrative is what is
            # wanted, and `view` on a directory has always worked.
            lines.append(
                f"{parent}/  ({len(members)} files - `view` this folder to list them)"
            )
        else:
            lines.append(f"{parent}/")
            lines.extend("  " + _label(f, f.path.rsplit("/", 1)[-1]) for f in members)
    return "\n".join(lines)


# ── Seed helpers ──────────────────────────────────────────────────────────────

async def ensure_seeded(user_id: int, db) -> None:
    """
    Idempotent backfill: create any default memory files the user is missing.
    Safe to call every turn — does nothing (one SELECT) once all defaults exist.
    Also backfills new default files added in later versions for existing users.
    """
    result = await db.execute(
        select(MemoryFile.path).where(MemoryFile.user_id == user_id)
    )
    existing = {row[0] for row in result.all()}
    from app.services.memory_spec import collection_from_monolith
    missing = []
    for path, content in INITIAL_FILES.items():
        coll = collection_from_monolith(path)
        if path in existing or (coll and any(p.startswith(coll.root + "/") for p in existing)):
            continue
        missing.append((path, content))
    if not missing:
        return
    for path, content in missing:
        db.add(MemoryFile(user_id=user_id, path=path, content=content))
    await db.commit()
    logger.info(
        "memory_files_seeded",
        extra={"user_id": user_id, "added": len(missing)},
    )


# ── Recall for context injection (used by orchestrator) ──────────────────────

class MemoryRecallCache:
    """Process-local cache backing recall_for_context / recall_sessions_for_context.

    One instance lives on app.state.memory_cache (Rule 6) — created in the
    lifespan and threaded through the orchestrator and the external chat proxy,
    rather than living as a bare module global.

    Two independent caches:
      - recall: the assembled memory block is stable within a session (memory
        rarely changes mid-conversation). Keyed on the max updated_at
        timestamp — if no memory file was written since the last recall, skip
        the full assembly and return the cached string.
      - episodic: frozen per-session block, computed once on a session's first
        turn and reused verbatim for the session's whole lifetime, bounded so
        process memory can't grow unboundedly across many sessions.
    """

    def __init__(self, episodic_max: int = 256) -> None:
        self._recall: dict[tuple[int, str], tuple[str, str]] = {}
        self._episodic: dict[int, str] = {}
        self._episodic_max = episodic_max

    def get_recall(self, key: tuple[int, str]) -> tuple[str, str] | None:
        return self._recall.get(key)

    def set_recall(self, key: tuple[int, str], watermark: str, block: str) -> None:
        self._recall[key] = (watermark, block)

    def get_episodic(self, session_id: int) -> str | None:
        return self._episodic.get(session_id)

    def set_episodic(self, session_id: int, block: str) -> None:
        if len(self._episodic) >= self._episodic_max:
            # Evict oldest inserted (dict preserves insertion order) — dead sessions.
            self._episodic.pop(next(iter(self._episodic)))
        self._episodic[session_id] = block


# ── Injection budget ─────────────────────────────────────────────────────────
# An injected file is re-sent on every turn of every session, so its size is
# paid in the one currency that cannot be cached: the model's attention. Money
# is not the issue — the block carries a cache breakpoint, so most turns read it
# at a tenth of input price — but a fact stated once inside 13.8 KB of prose
# competes with everything else in the prompt, and it stopped winning as the
# prose grew. That is what the owner reported as having to explain simple things
# twice.
#
# `memory_schema.INJECTED_FILE_MAX_BYTES` declares a 12 KB ceiling and was only
# ever checked on WRITE, never at injection, so owner.md sat 1.7 KB over it and
# nothing stopped it growing further. This is the injection-time half.
#
# Truncation is section-aware and works from the MIDDLE OUTWARD, which is the
# whole point. owner.md's shape is typical of a narrative memory file:
#
#     Origins … Uludağ … Istanbul … Ankara … Employment History   ← 12,640 chars
#     Communication style                                          ←    180 chars
#     Explicit instructions                                        ←    840 chars
#
# The directives that govern how every agent behaves are the LAST kilobyte, and
# a naive head-truncation to fit a budget would delete them and keep the
# chronology. So the tail is kept first, then the head for identity, and the
# middle — the narrative, which is now individually retrievable as observations
# and injected on demand by app/services/relevant_recall.py — is what gives way.
#
# Nothing is lost: the file is untouched on disk and the elision marker names
# what was dropped and how to read it.

_SECTION_SPLIT = re.compile(r"^(?=## )", re.MULTILINE)


def _split_sections(content: str) -> list[str]:
    """A document into its `## ` sections, preamble first, order preserved."""
    parts = [p for p in _SECTION_SPLIT.split(content or "") if p.strip()]
    return parts or ([content] if content else [])


def _heading_of(section: str) -> str:
    first = section.strip().splitlines()[0] if section.strip() else ""
    return first.lstrip("# ").strip() or "untitled section"


def elide_middle(path: str, content: str, budget: int) -> str:
    """`content` trimmed to roughly `budget` characters, middle sections first.

    Keeps whole sections — a document cut mid-sentence reads as corrupted and
    invites the model to guess at the rest. Priority is the TAIL, then the head,
    because in a narrative memory file the operational directives live at the
    end and the story lives in the middle. Returns `content` unchanged when it
    already fits or when there is nothing safe to drop.
    """
    body = (content or "").strip()
    if budget <= 0 or len(body) <= budget:
        return body

    sections = _split_sections(body)
    if len(sections) < 3:
        # Nothing to elide without cutting into a section. Sending it whole and
        # over budget beats sending half a sentence.
        return body

    keep: set[int] = set()
    used = 0
    # Tail first, then head, alternating inward. The preamble (index 0) is taken
    # in the head pass like any other section.
    head, tail = 0, len(sections) - 1
    take_tail = True
    while head <= tail:
        i = tail if take_tail else head
        size = len(sections[i])
        if used + size > budget and keep:
            break
        keep.add(i)
        used += size
        if take_tail:
            tail -= 1
        else:
            head += 1
        take_tail = not take_tail

    dropped = [i for i in range(len(sections)) if i not in keep]
    if not dropped:
        return body

    out: list[str] = []
    marked = False
    for i, section in enumerate(sections):
        if i in keep:
            out.append(section.strip())
            marked = False
            continue
        if not marked:
            names = ", ".join(f"“{_heading_of(sections[d])}”" for d in dropped[:8])
            more = f" and {len(dropped) - 8} more" if len(dropped) > 8 else ""
            out.append(
                f"_[{len(dropped)} section(s) not shown here to keep this prompt "
                f"legible: {names}{more}. They are NOT deleted — the full file is "
                f"at `{path}`, readable with the `memory` tool, and the facts "
                f"inside it are individually searchable with `search_memory`. "
                f"Open it when the answer needs the narrative rather than a "
                f"fact.]_"
            )
            marked = True
    return "\n\n".join(out)


def bounded_excerpt(path: str, content: str, budget: int) -> str:
    """Keep the prompt budget even when one section exceeds the whole cap."""
    excerpt = elide_middle(path, content, budget)
    if budget <= 0 or len(excerpt) <= budget:
        return excerpt
    marker = f"\n\n_[Excerpt shortened; open `{path}` with `memory` for the full record.]_\n\n"
    room = max(0, budget - len(marker))
    if room == 0:
        return marker[:budget]
    head = room // 2
    return excerpt[:head] + marker + excerpt[-(room - head):]


async def recall_for_context(user_id: int, db, agent_id: str = "speda", *, cache: MemoryRecallCache) -> str:
    """
    Load the memory context to prepend to the system prompt.
    Returns a directory listing and the small standing-memory set. Relevant
    narrative and domain records are selected per turn by
    relevant_files_for_message, or read explicitly with the memory tool.
    """
    await ensure_seeded(user_id, db)

    result = await db.execute(
        select(MemoryFile).where(MemoryFile.user_id == user_id)
    )
    all_files = list(result.scalars().all())

    # Keep the stable prefix to standing constraints and the current-state view.
    # Biography, history, patterns and domain files are retrieved for the actual
    # user message below, not sent to every agent on every turn.
    source_file = source_file_for(agent_id)

    # Watermark: if no file changed since last recall, return the cached block.
    # Keyed by (user_id, agent_id) — different agents preload different files.
    # The source-file assignment is part of the key/watermark so reassigning it
    # from the UI (no file change) still refreshes the injected block.
    #
    # Only the files that are actually IN the block count. It used to be every
    # file in the store, and /memories/log.md is rewritten by update_session_log
    # on every single turn while appearing in the block only as a name in the
    # directory listing — so the watermark moved every turn and this cache never
    # once hit. The recomputed bytes were usually identical, so the provider's
    # prompt cache still held and no tokens were wasted; what was wasted was
    # rebuilding the listing and re-eliding every injected file, every turn,
    # to arrive back at the same string.
    #
    # The listing is a function of the sorted PATH SET, not of content, so it is
    # tracked by the path set rather than by anyone's updated_at.
    from app.core.clock import owner_today
    from app.services.memory_states import project_files
    all_files = project_files(all_files, owner_today())
    by_path = {f.path: f for f in all_files}

    # Resolved against what actually exists: a split injected document
    # contributes its members, an unsplit one still contributes itself. Needed
    # HERE, above the watermark, because which files are injected is what
    # decides which files the watermark may watch.
    standing = {
        "/memories/owner.md", "/memories/current.md", "/memories/dossier.md",
        "/memories/dossier/prohibitions.md", "/memories/dossier/dislikes.md",
        "/memories/dossier/wants.md",
    }
    preload = [p for p in preload_paths(set(by_path)) if p in standing]

    injected = set(preload)
    watermarked = [f for f in all_files if f.path in injected]
    watermark = (
        max((f.updated_at.isoformat() for f in watermarked), default="")
        + f"|{source_file or ''}|{owner_today().isoformat()}"
        + "|" + str(hash(tuple((f.path, f.content) for f in all_files if f.path == "/memories/current.md")))
        + f"|{len(all_files)}|{hash(tuple(sorted(f.path for f in all_files)))}"
    )
    cache_key = (user_id, agent_id)
    cached = cache.get_recall(cache_key)
    if cached and cached[0] == watermark:
        return cached[1]

    from app.config import settings

    # Size-free listing keeps this recall block byte-stable across turns so the
    # prompt cache holds (file sizes otherwise change every turn as log.md grows).
    listing = _format_directory(
        all_files,
        MEMORY_ROOT,
        with_sizes=False,
        collapse=settings.memory_directory_collapse_above,
    )

    # Injected files are capped at injection time, not just on write — see
    # elide_middle(). A file that outgrew its budget gives up its middle,
    # keeping the directives at its end.
    budget = settings.memory_injected_file_max_chars
    sections = [f"### Directory\n\n{listing}"]
    for path in preload:
        f = by_path.get(path)
        if f:
            cap = min(budget, 1800) if path == "/memories/owner.md" and budget else budget
            sections.append(f"### {path}\n\n{bounded_excerpt(path, f.content, cap)}")

    body = "\n\n".join(sections)

    # Source-of-truth directive — the one file this agent owns for reading AND
    # writing its domain data. Placed last so it's the strongest instruction.
    source_directive = ""
    if source_file:
        from app.services.memory_spec import collection_by_root

        coll = collection_by_root(source_file)
        if coll is not None:
            members = ", ".join(f"`{m.stem}`" for m in coll.members)
            dated = next((m for m in coll.members if m.index_pattern), None)
            # Name the log explicitly. It is the one file NOT in the prompt, so
            # an agent that is not told it exists will either answer without it
            # or re-read the whole domain looking for it.
            # A sharded log is a FOLDER of keys, and naming it as a file would
            # send the agent to a path that does not exist — the one thing this
            # line exists to prevent.
            where = (
                f"`{coll.root}/{dated.stem}/` — one file per "
                f"{dated.key_shape or dated.index_pattern}"
                if dated and dated.shard
                else f"`{coll.root}/{dated.stem}.md`" if dated else ""
            )
            log_line = (
                f" The dated log {where} is deliberately NOT preloaded — it grows "
                f"without bound. Open the one you need with `view` when you need "
                f"past entries, and add to it with `ledger_append` (give the "
                f"key; it finds the file, and creates it if the key is new)."
                if dated else ""
            )
            source_directive = (
                f"\n\n**Your domain is `{coll.root}/` — {members}.** Its documents "
                "are available by path, but are not all preloaded. Relevant ones "
                "may appear in the per-turn recall block; otherwise open the "
                f"specific document with `memory` before relying on it.{log_line} "
                "Keep durable updates in their owning record using the designated "
                "write tool; never leave a confirmed change only in conversation."
            )
        else:
            source_directive = (
                f"\n\n**Your source of truth is `{source_file}`.** Open it with "
                "`memory` when the question needs its detail and it was not recalled "
                "this turn. Use the designated evidence-bound write tool for "
                "confirmed updates; raw memory edits are disabled."
            )

    from app.services.memory_policy import routing_contract
    source_directive += "\n\n" + routing_contract()
    block = (
        "## Memory\n\n"
        "This is shared knowledge about your OWNER, maintained across all of your "
        "sessions. It describes HIM — his profile, what is current for him, and how "
        "he likes to be treated. It does NOT define who you are: your own identity, "
        "name and role are set above and are unaffected by anything in this section. "
        "Read it as notes about the owner, never as a description of yourself.\n\n"
        f"{body}\n\n"
        "Only the files shown above are preloaded; do not re-read them. Other "
        "files are selected per turn or opened on demand. The Directory lists "
        "every path that exists: projects and people are ONE "
        "FILE EACH (`/memories/projects/<name>.md`, "
        "`/memories/social/<category>/<name>.md`), so open the single entity the "
        "task is about rather than a folder or a whole domain file. dossier.md "
        "shapes how you respond — act on it, never cite it aloud."
        f"{source_directive}"
    )
    cache.set_recall(cache_key, watermark, block)
    return block


async def relevant_files_for_message(user_id: int, db, query: str) -> str:
    """Select at most two grounded documents for this turn, with a hard size cap.

    No model call or embedding dependency is needed: exact course codes and
    document names are reliable anchors. A miss is explicit; the agent can use
    `memory`, `read_course_memory`, or `search_memory` for a deliberate lookup.
    """
    from app.config import settings
    from app.services.memory_spec import is_course_path

    query = (query or "").strip()[:2000]
    if len(query) < 6:
        return ""
    ignored = {"about", "after", "again", "bana", "benim", "ders", "dersi", "dersin",
               "from", "hangi", "icin", "için", "ile", "more", "nasıl", "nedir",
               "olan", "that", "this", "what", "when", "where", "your"}
    terms = {term for term in re.findall(r"[^\W_]{3,}", query.casefold()) if term not in ignored}
    if not terms:
        return ""
    rows = (await db.execute(select(MemoryFile).where(MemoryFile.user_id == user_id))).scalars().all()
    always = {"/memories/current.md", "/memories/dossier.md",
              "/memories/dossier/prohibitions.md", "/memories/dossier/dislikes.md",
              "/memories/dossier/wants.md"}
    codes = {code.upper() for code in re.findall(r"\b[A-Za-z]{2,8}\d{2,4}[A-Za-z]?\b", query)}
    term = re.search(r"\b\d{4}-\d{4}-(?:spring|summer|fall)\b", query.casefold())
    if codes:
        matching_courses = sorted((row.path for row in rows if is_course_path(row.path)
                                   and row.path.rsplit("/", 1)[-1][:-3] in codes
                                   and (not term or f"/{term.group(0)}/" in row.path)), reverse=True)
        if len(matching_courses) > 1 and not term:
            heading = ("## Course record choices\n\nThe same code exists in several terms. "
                       "Use `read_course_memory` with the correct term before answering:\n")
            choices = []
            used = len(heading)
            for path in matching_courses:
                if used + len(path) + 1 > 5700:
                    break
                choices.append(path)
                used += len(path) + 1
            return heading + "\n".join(choices) + (
                "\nMore terms exist; list them with `read_course_memory`."
                if len(choices) < len(matching_courses) else ""
            )

    ranked = []
    for row in rows:
        path = row.path
        if path in always or "/." in path or path == "/memories/log.md":
            continue
        path_terms = set(re.findall(r"[^\W_]{3,}", path.casefold()))
        title = row.content.split("\n", 1)[0].casefold()
        title_terms = set(re.findall(r"[^\W_]{3,}", title))
        body_terms = set(re.findall(r"[^\W_]{3,}", row.content[:12000].casefold()))
        score = 10 * len(terms & path_terms) + 8 * len(terms & title_terms) + min(3, len(terms & body_terms))
        if path == "/memories/owner.md" and terms & {
            "biyografi", "çocukluğum", "geçmişim", "kimim", "hayatım", "origins",
        }:
            score += 12
        if path == "/memories/history.md" and terms & {"geçmiş", "eskiden", "önceden", "history"}:
            score += 12
        if is_course_path(path) and path.rsplit("/", 1)[-1][:-3] in codes:
            score += 30
        if score >= 8:
            ranked.append((score, path, row.content))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    preamble = ("## Relevant memory documents\n\nThese are retrieved candidates, not proof that "
                "they answer the question. Check the term, date and source before using them; "
                "open the exact path with `memory` if the excerpt is incomplete.\n\n")
    budget = 6000 - len(preamble)
    sections = []
    for _score, path, content in ranked[:2]:
        header = f"### {path}\n\n"
        gap = 2 if sections else 0
        available = min(settings.memory_injected_file_max_chars or budget, budget - gap - len(header))
        if available < 200:
            break
        section = header + bounded_excerpt(path, content, available)
        sections.append(section)
        budget -= gap + len(section)
    if not sections:
        return ""
    return preamble + "\n\n".join(sections)


def document_query_for_history(history) -> str:
    """Carry a prior course code into a short follow-up without carrying prose."""
    from app.services.relevant_recall import initial_recall_query

    users = [message for message in history or []
             if isinstance(message, dict) and message.get("role") == "user"]
    latest = initial_recall_query(users[-1:])
    if len(users) < 2 or len(latest) > 100 or re.search(r"\b(?:başka|different|other)\b", latest.casefold()):
        return latest
    if re.search(r"\b[A-Za-z]{2,8}\d{2,4}[A-Za-z]?\b", latest):
        return latest
    if not re.search(r"\b(?:ders\w*|sınav\w*|notlar?\w*|ödev\w*|course\w*|exam\w*|lecture\w*)\b", latest.casefold()):
        return latest
    previous = initial_recall_query(users[:-1])
    code = re.search(r"\b[A-Za-z]{2,8}\d{2,4}[A-Za-z]?\b", previous)
    term = re.search(r"\b\d{4}-\d{4}-(?:spring|summer|fall)\b", previous.casefold())
    return " ".join(part for part in (latest, code.group(0) if code else "",
                                     term.group(0) if term else "") if part)


async def today_across_sessions_for_context(
    user_id: int, db, agent_id: str, session_id: int, *, scope: str = "own",
    query: str = "",
) -> str:
    """Bounded, live continuity from persisted owner messages, independent of recaps.

    A new chat must see what the owner said earlier today even if recap generation
    is delayed or fails. This is deliberately queried every turn, not frozen in
    MemoryRecallCache: messages from another active chat may arrive meanwhile.
    """
    from app.core.clock import owner_today, owner_tz, to_owner
    from app.models.message import Message
    from app.models.session import Session

    local_start = datetime.combine(owner_today(), time.min, tzinfo=owner_tz())
    utc_start = local_start.astimezone(timezone.utc).replace(tzinfo=None)
    conditions = [
        Session.user_id == user_id,
        Session.id != session_id,
        Session.triggered_by == "user",
        Message.role == "user",
        Message.created_at >= utc_start,
    ]
    if scope != "all":
        conditions.append(Session.agent_id == agent_id)
    rows = (await db.execute(
        select(Message, Session.agent_id)
        .join(Session, Message.session_id == Session.id)
        .where(*conditions)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(120)
    )).all()
    if not rows:
        return ""

    from app.services.relevant_recall import _extract_text, _strip_stamp

    query_terms = {word for word in re.findall(r"[^\W_]{4,}", query.casefold())
                   if word not in {"about", "again", "bana", "benim", "hangi", "nasıl", "what"}}
    candidates = []
    for rank, (message, source_agent) in enumerate(rows):
        content = _strip_stamp(_extract_text(message.content)).strip()
        if not content:
            continue
        words = set(re.findall(r"[^\W_]{4,}", content.casefold()))
        score = sum(any(word.startswith(term) or term.startswith(word)
                        for word in words) for term in query_terms)
        candidates.append((score, rank, message, source_agent, content))
    # Four query matches plus four newest messages give a new chat both the
    # ongoing thread and a way back to older same-day details.
    matching = sorted((item for item in candidates if item[0]),
                      key=lambda item: (-item[0], item[1]))[:4]
    selected = matching + [item for item in candidates if item not in matching][:8-len(matching)]
    entries = []
    used = 0
    for _score, _rank, message, source_agent, content in selected:
        content = " ".join(content.split())[:420]
        stamp = to_owner(message.created_at).strftime("%H:%M")
        entry = f"[{stamp} {source_agent} message:{message.id}] {content}"
        if used + len(entry) > 2600:
            break
        entries.append(entry)
        used += len(entry)
        if len(entries) >= 8:
            break
    if not entries:
        return ""
    return (
        "## Today in other conversations\n\n"
        "Recent owner messages from separate chats today (owner-local date). "
        "These are source excerpts, not a complete transcript or proof of an "
        "outcome. Follow their message ids with `recall_conversations` when "
        "the exact exchange or reply matters. Do not ask the owner to repeat "
        "what is already here.\n\n" + "\n".join(entries)
    )


# ── Episodic recall: recent-session recaps (used by orchestrator) ─────────────

# Frozen per-session block: computed on a session's FIRST turn and reused
# verbatim for the session's whole lifetime ("" cached too). This guarantees
# byte-stability of the injected system block within a session — the block is
# deliberately UNCACHED at the API level (all 4 cache breakpoints are spent),
# so it must never change mid-session or the 5m conversation cache would bust
# every turn. New sessions always miss this cache and read fresh recaps.
# (Cache storage itself lives in MemoryRecallCache above — Rule 6.)

# Legacy fallback: sessions that predate the recap feature may still have a
# compaction summary — better than nothing, truncated hard.
_FALLBACK_SUMMARY_CHARS = 600


async def recall_sessions_for_context(
    user_id: int,
    db,
    agent_id: str,
    session_id: int,
    cache: MemoryRecallCache,
    scope: str = "own",
) -> str:
    """
    Build the "## Previous sessions" episodic block for a session: recaps of the
    owner's most recent OTHER sessions, newest first, so a brand-new session can
    answer "what were we discussing last time?" without any tool call.

    scope="own" (default) sees only this agent's sessions; scope="all" (the
    orchestrator profile) sees every agent's, tagged by agent_id. Returns ""
    when disabled or when there is nothing to recall.
    """
    from app.config import settings
    from app.models.session import Session

    if not settings.episodic_recap_enabled:
        return ""

    cached = cache.get_episodic(session_id)
    if cached is not None:
        return cached

    conditions = [
        Session.user_id == user_id,
        Session.id != session_id,
        (Session.recap.isnot(None)) | (Session.summary.isnot(None)),
    ]
    if scope != "all":
        conditions.append(Session.agent_id == agent_id)

    result = await db.execute(
        select(Session)
        .where(*conditions)
        .order_by(Session.started_at.desc())
        .limit(settings.episodic_recall_sessions)
    )
    sessions = list(result.scalars().all())

    entries: list[str] = []
    for s in sessions:
        body = (s.recap or "").strip()
        if not body:
            body = (s.summary or "").strip()[:_FALLBACK_SUMMARY_CHARS]
        if not body:
            continue
        date = s.started_at.strftime("%Y-%m-%d") if s.started_at else "?"
        title = (s.title or "Untitled").strip()
        tag = f"[{s.agent_id}] " if scope == "all" else ""
        entries.append(f"### {date} — {tag}{title}\n{body}")

    block = ""
    if entries:
        # Newest-first; drop oldest entries to stay under the char budget.
        budget = settings.episodic_recall_max_chars
        kept: list[str] = []
        used = 0
        for e in entries:
            if used + len(e) > budget and kept:
                break
            kept.append(e)
            used += len(e)
        block = (
            "## Previous sessions\n\n"
            "Recaps of your most recent separate conversations with the owner, "
            "newest first. This is episodic background you two already share: "
            "when he asks what you were discussing or where you left off, answer "
            "from these directly — do not call a tool for what is already here. "
            "These cover only the last few sessions in brief; for older material "
            "or verbatim detail, use `recall_conversations`.\n\n"
            + "\n\n".join(kept)
        )

    cache.set_episodic(session_id, block)
    return block


# ── The skill ─────────────────────────────────────────────────────────────────

class MemorySkill(Skill):
    """
    On-demand reader for persistent owner-memory documents.

    This complements the small standing set and per-turn retrieval without
    reloading the whole filesystem into every prompt.
    """

    name = "memory"
    description = (
        "Read a specific persisted memory document or list a directory under /memories. "
        "Use `view` when the small standing block and per-turn recalled documents do not "
        "contain the detail needed for this task; open the exact course, topic, person or "
        "project instead of a whole folder when its path is known. "
        "Do NOT use create, str_replace, insert or delete for agent writes: raw edits are "
        "disabled, and confirmed changes need their evidence-bound domain tool. "
        "Returns the document with line numbers or a directory listing; a missing path "
        "is reported explicitly rather than filled from another record."
    )
    read_only = True
    input_schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "enum": ["view"],
                "description": "view: list a directory or read a file, optionally by line range.",
            },
            "path": {
                "type": "string",
                "description": "File or directory path. Must start with /memories.",
            },
            "view_range": {
                "type": "array",
                "items": {"type": "integer"},
                "minItems": 2,
                "maxItems": 2,
                "description": "Optional [start_line, end_line] range for view.",
            },
        },
        "required": ["command", "path"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        command = args.get("command", "")
        if command != "view":
            return "Raw memory writes are disabled. Use the evidence-bound domain tool or record_observation."
        path = args.get("path", "").rstrip("/")
        db = context.db
        user_id = context.user_id

        err = _validate_path(path)
        if err:
            return err

        return await self._view(path, args, user_id, db)

    # ── Command handlers ──────────────────────────────────────────────────────

    async def _view(self, path: str, args: dict, user_id: int, db) -> str:
        # Check if it's the root or a directory prefix
        is_dir = path == MEMORY_ROOT or not path.endswith(".md")

        if is_dir:
            # Match on the directory prefix WITH its trailing slash. A bare
            # `startswith("/memories/projects")` also matches
            # "/memories/projects.md", so listing the folder used to include the
            # monolith it replaced — the one file whose content is duplicated
            # across every member of that folder.
            prefix = MEMORY_ROOT if path == MEMORY_ROOT else path.rstrip("/") + "/"
            result = await db.execute(
                select(MemoryFile).where(
                    MemoryFile.user_id == user_id,
                    MemoryFile.path.startswith(prefix),
                )
            )
            files = result.scalars().all()
            return _format_directory(list(files), path)

        # Single file
        result = await db.execute(
            select(MemoryFile).where(
                MemoryFile.user_id == user_id,
                MemoryFile.path == path,
            )
        )
        file = result.scalar_one_or_none()
        if file is None:
            return f"The path {path} does not exist. Please provide a valid path."

        content = file.content
        if path == "/memories/current.md":
            from app.services.memory_states import effective_current
            content = await effective_current(db, user_id)
        view_range = args.get("view_range")
        if view_range:
            lines = content.splitlines()
            start, end = view_range[0] - 1, view_range[1]
            content = "\n".join(lines[start:end])

        return _format_file_with_lines(path, content)

    async def _create(self, path: str, args: dict, context: AgentContext) -> str:
        user_id, db = context.user_id, context.db
        if not path.endswith(".md") and "." not in path.split("/")[-1]:
            path = path + ".md"

        result = await db.execute(
            select(MemoryFile).where(
                MemoryFile.user_id == user_id,
                MemoryFile.path == path,
            )
        )
        if result.scalar_one_or_none() is not None:
            return f"Error: File {path} already exists. Use str_replace to update it."

        content = args.get("file_text", "")
        try:
            note = _schema_note(
                path=path, before="", after=content,
                is_create=True, agent_id=context.agent_id,
            )
        except MemorySchemaViolation as e:
            return str(e)

        try:
            await mutate_file(db, user_id=user_id, path=path, author=context.agent_id,
                              action="create", before=None, after=content,
                              request_id=context.request_id)
        except (MemoryWriteConflict, MemorySchemaViolation) as e:
            return str(e)
        logger.info("memory_file_created", extra={"user_id": user_id, "path": path})
        return f"File created successfully at: {path}{note}"

    async def _str_replace(self, path: str, args: dict, context: AgentContext) -> str:
        user_id, db = context.user_id, context.db
        result = await db.execute(
            select(MemoryFile).where(
                MemoryFile.user_id == user_id,
                MemoryFile.path == path,
            )
        )
        file = result.scalar_one_or_none()
        if file is None:
            return f"Error: The path {path} does not exist. Please provide a valid path."

        old_str = args.get("old_str", "")
        new_str = args.get("new_str", "")

        if not old_str:
            return "Error: old_str must not be empty."

        count = file.content.count(old_str)
        if count == 0:
            return f"No replacement was performed, old_str `{old_str}` did not appear verbatim in {path}."
        if count > 1:
            # Find line numbers of occurrences
            lines = file.content.splitlines()
            hits = [str(i + 1) for i, line in enumerate(lines) if old_str in line]
            return (
                f"No replacement was performed. Multiple occurrences of old_str "
                f"`{old_str}` in lines: {', '.join(hits)}. Please ensure it is unique."
            )

        before = file.content
        candidate = file.content.replace(old_str, new_str, 1)
        try:
            note = _schema_note(
                path=path, before=before, after=candidate,
                is_create=False, agent_id=context.agent_id,
            )
        except MemorySchemaViolation as e:
            return str(e)

        try:
            await mutate_file(db, user_id=user_id, path=path, author=context.agent_id,
                              action="str_replace", before=before, after=candidate,
                              request_id=context.request_id)
        except (MemoryWriteConflict, MemorySchemaViolation) as e:
            return str(e)

        # Return snippet around the change
        snippet = _format_file_with_lines(path, candidate)
        return f"The memory file has been edited.\n{snippet}{note}"

    async def _insert(self, path: str, args: dict, context: AgentContext) -> str:
        user_id, db = context.user_id, context.db
        result = await db.execute(
            select(MemoryFile).where(
                MemoryFile.user_id == user_id,
                MemoryFile.path == path,
            )
        )
        file = result.scalar_one_or_none()
        if file is None:
            return f"Error: The path {path} does not exist."

        insert_line = args.get("insert_line", 0)
        insert_text = args.get("insert_text", "")
        lines = file.content.splitlines()
        n = len(lines)

        if insert_line < 0 or insert_line > n:
            return (
                f"Error: Invalid `insert_line` parameter: {insert_line}. "
                f"It should be within the range of lines of the file: [0, {n}]"
            )

        before = file.content
        lines.insert(insert_line, insert_text.rstrip("\n"))
        candidate = "\n".join(lines)
        try:
            note = _schema_note(
                path=path, before=before, after=candidate,
                is_create=False, agent_id=context.agent_id,
            )
        except MemorySchemaViolation as e:
            return str(e)

        try:
            await mutate_file(db, user_id=user_id, path=path, author=context.agent_id,
                              action="insert", before=before, after=candidate,
                              request_id=context.request_id)
        except (MemoryWriteConflict, MemorySchemaViolation) as e:
            return str(e)
        return f"The file {path} has been edited.{note}"

    async def _delete(self, path: str, context: AgentContext) -> str:
        user_id, db = context.user_id, context.db
        result = await db.execute(
            select(MemoryFile).where(
                MemoryFile.user_id == user_id,
                MemoryFile.path == path,
            )
        )
        file = result.scalar_one_or_none()
        if file is None:
            return f"Error: The path {path} does not exist."

        # The fixed set and a CLOSED collection's declared members are closed in
        # BOTH directions: nothing may be added and none of it may be removed.
        # Deleting one would not lose the content — it is versioned — but it
        # would leave the routing tree pointing at a file that no longer exists,
        # and the next agent with a fact for it has nowhere legal to put it. An
        # OPEN entity collection (`projects/`, `social/`) is not part of that
        # set — its members are meant to be added AND removed, which is what
        # lets Orion delete the stray half of a duplicate project or person file.
        from app.services.memory_schema import is_removable

        if not is_removable(path):
            return (
                f"Error: `{path}` is one of the canonical memory files and cannot be "
                f"deleted — the taxonomy is closed in both directions. To empty it, "
                f"demote its contents to the right file per the routing tree and leave "
                f"the file itself in place with its header."
            )

        before = file.content
        try:
            await mutate_file(db, user_id=user_id, path=path, author=context.agent_id,
                              action="delete", before=before, after=None,
                              request_id=context.request_id)
        except (MemoryWriteConflict, MemorySchemaViolation) as e:
            return str(e)
        logger.info("memory_file_deleted", extra={"user_id": user_id, "path": path})
        return f"Successfully deleted {path}"
