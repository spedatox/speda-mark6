"""
Speda Memory Skill — implements Anthropic's agent memory tool pattern.

Architecture (per Anthropic Memory Tool docs):
  - Memory is a virtual filesystem: structured markdown files under /memories/
  - Speda reads its memory directory at the start of every turn (JIT retrieval)
  - Speda writes and updates memory files when it learns something worth keeping
  - The agent controls its own memory — passive background extraction supplements this
    but the primary write path is Speda itself during conversations

Commands (matching Anthropic's spec exactly):
  view       → list directory or read file with line numbers
  create     → create new file (error if exists)
  str_replace → replace a unique string in a file
  insert     → insert text after a line number
  delete     → delete a file
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select, delete as sql_delete

from app.core.context import AgentContext
from app.models.memory_file import MemoryFile
from app.services.memory_schema import MemorySchemaViolation, check_write
from app.services.memory_store import record_revision
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

_Last updated: (never)_

(Refreshed once per day: a short snapshot of what is genuinely current in the
owner's life. Finished or stale items are moved OUT, not kept. Trust this for
recency — never present something absent here as new.)
""",
    "/memories/dossier.md": """\
# Dossier — what we've observed about how he wants to be treated

_The agents' working model of the owner's preferences, built as they talk to him:
what he likes, dislikes, and wants — and in what manner. Both stated preferences
and inferred patterns. Every entry is attributed and dated: `- [YYYY-MM-DD,
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
    "/memories/patterns.md": """\n# Patterns — what he repeatedly does, and what to do about it

_Induced, not stated. Every line is `- [YYYY-MM-DD, agent_id, confidence] the
pattern → the move it calls for`, where confidence is high (5+ supporting
facts), medium (3-4) or low (2). This file is in front of every agent on every
turn for one reason: a pattern is only worth anything BEFORE it fires. Act on it
silently, the way you act on the dossier. It is induced from evidence and can be
wrong, so a low-confidence line is a hypothesis to watch, not a fact to assert —
and never read one aloud or cite it to him._

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
    "centurion": "/memories/cybersec",
    "orion": "/memories/ops",
}


def source_file_for(agent_id: str) -> str | None:
    """The agent's active source of truth — now a DIRECTORY, not a file.

    The owner's runtime override wins (he can still pin a single file from the
    Configuration tab), then the built-in default, then None.
    """
    from app.core.runtime_state import get_agent_sources

    return get_agent_sources().get(agent_id) or AGENT_SOURCE_DEFAULTS.get(agent_id)


def source_preload_for(agent_id: str) -> list[str]:
    """Which of the agent's own files to put in the prompt every turn.

    Its STANDING context — the profile, the program, the runbook — and never the
    dated log, which is the one member that grows without bound. Preloading
    `wellness.md` whole meant Atomix paid 27 KB every turn to be told his own
    training split, and 14 KB of that was a session log he re-read from the top
    on every request. The log is one `view` away and he is told its exact path.

    A single-file override (the owner pinning one path) is returned as-is.
    """
    from app.services.memory_spec import collection_by_root

    src = source_file_for(agent_id)
    if not src:
        return []
    coll = collection_by_root(src)
    if coll is None:
        return [src]
    return [
        f"{coll.root}/{m.stem}.md" for m in coll.members
        if not m.index_pattern      # the dated log stays on demand
    ]


# ── Path validation ───────────────────────────────────────────────────────────

def _validate_path(path: str) -> str | None:
    """Return error string if path is invalid, None if OK."""
    if not path.startswith(MEMORY_ROOT):
        return f"Error: Path must start with {MEMORY_ROOT}. Got: {path}"
    if ".." in path:
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


def _format_directory(files: list[MemoryFile], path: str, *, with_sizes: bool = True) -> str:
    """Render a file listing, grouped by folder.

    Grouping is not cosmetic. Since projects and people became one file each
    (memory_spec.COLLECTIONS) this listing carries ~40 paths instead of ~14, and
    it is injected into EVERY turn for EVERY agent — repeating
    `/memories/social/professional/` on each line would spend a few hundred
    tokens per request restating a prefix. It is also what makes the listing an
    index the model can actually use: every project and person is visible by
    name, so the right file can be opened directly instead of searched for.

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
    # far as the agent is concerned. `life/` is empty until the first document
    # that belongs to no domain arrives — and the prompt tells agents to file
    # such a document there, while this listing is what they trust for what
    # exists. The two disagreeing is how an instruction quietly stops being
    # followed.
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
    missing = [(p, c) for p, c in INITIAL_FILES.items() if p not in existing]
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


async def recall_for_context(user_id: int, db, agent_id: str = "speda", *, cache: MemoryRecallCache) -> str:
    """
    Load the memory context to prepend to the system prompt.
    Returns: directory listing (so the agent knows what exists) + the preloaded
    set (owner/current/dossier/history, plus any per-agent working file such as
    Atomix's sessions.md). The agent reads the remaining files JIT during the
    conversation via the memory tool.
    """
    await ensure_seeded(user_id, db)

    result = await db.execute(
        select(MemoryFile).where(MemoryFile.user_id == user_id)
    )
    all_files = list(result.scalars().all())

    # The agent's source-of-truth file (owner-configurable) is preloaded up front
    # AND flagged as its read/write target below.
    source_file = source_file_for(agent_id)

    # Watermark: if no file changed since last recall, return the cached block.
    # Keyed by (user_id, agent_id) — different agents preload different files.
    # The source-file assignment is part of the key/watermark so reassigning it
    # from the UI (no file change) still refreshes the injected block.
    watermark = max((f.updated_at.isoformat() for f in all_files), default="") + f"|{source_file or ''}"
    cache_key = (user_id, agent_id)
    cached = cache.get_recall(cache_key)
    if cached and cached[0] == watermark:
        return cached[1]

    by_path = {f.path: f for f in all_files}

    # Size-free listing keeps this recall block byte-stable across turns so the
    # prompt cache holds (file sizes otherwise change every turn as log.md grows).
    listing = _format_directory(all_files, MEMORY_ROOT, with_sizes=False)

    # Resolved against what actually exists: a split injected document
    # contributes its members, an unsplit one still contributes itself.
    preload = preload_paths(set(by_path))
    for p in source_preload_for(agent_id):
        if p not in preload:
            preload.append(p)
    sections = [f"### Directory\n\n{listing}"]
    for path in preload:
        f = by_path.get(path)
        if f:
            sections.append(f"### {path}\n\n{f.content.strip()}")

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
            log_line = (
                f" The dated log `{coll.root}/{dated.stem}.md` is deliberately NOT "
                f"preloaded — it grows without bound. Open it with `view` when you "
                f"need past entries, and add to it with `ledger_append` (give the "
                f"date; it finds the file)."
                if dated else ""
            )
            source_directive = (
                f"\n\n**Your domain is `{coll.root}/` — {members}.** Everything above "
                f"from that folder is already in your context; do not re-read it."
                f"{log_line} These files are the AUTHORITATIVE record for your "
                f"domain: report every figure from them, and write every update back "
                f"in the same turn you learn it. Never leave them stale, never keep "
                f"your domain data only in the conversation, and never create a file "
                f"the folder does not already have."
            )
        else:
            source_directive = (
                f"\n\n**Your source of truth is `{source_file}`.** It is preloaded above. "
                f"Treat it as the AUTHORITATIVE record for your domain: read every figure "
                f"and fact you report from it, and write EVERY update, change, or new entry "
                f"back to it with the `memory` tool (str_replace/insert/create) in the same "
                f"turn you learn it — never leave it stale and never keep your domain data "
                f"only in the conversation."
            )

    block = (
        "## Memory\n\n"
        "This is shared knowledge about your OWNER, maintained across all of your "
        "sessions. It describes HIM — his profile, what is current for him, and how "
        "he likes to be treated. It does NOT define who you are: your own identity, "
        "name and role are set above and are unaffected by anything in this section. "
        "Read it as notes about the owner, never as a description of yourself.\n\n"
        f"{body}\n\n"
        "Every file above is already here — do not re-read it. For anything else, "
        "the Directory lists every path that exists: projects and people are ONE "
        "FILE EACH (`/memories/projects/<name>.md`, "
        "`/memories/social/<category>/<name>.md`), so open the single entity the "
        "task is about rather than a folder or a whole domain file. dossier.md "
        "shapes how you respond — act on it, never cite it aloud."
        f"{source_directive}"
    )
    cache.set_recall(cache_key, watermark, block)
    return block


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
    Speda's persistent memory tool.
    Implements Anthropic's agent memory pattern: view/create/str_replace/insert/delete.
    Speda uses this to maintain continuity across sessions without reloading
    everything into the context window upfront.
    """

    name = "memory"
    description = (
        "Read or write the owner's persistent memory files under /memories. "
        "owner.md, current.md, dossier.md and history.md are ALREADY in your context every "
        "turn — never use this tool to read them. Use 'view' only to open a SPECIFIC other "
        "file when the task needs detail you don't already have: ONE project "
        "(/memories/projects/<name>.md), ONE person "
        "(/memories/social/<category>/<name>.md), finance.md, wellness.md, academic.md or "
        "log.md. The exact filenames are in the directory listing already in your context, so "
        "read the one entity you need rather than a whole folder — that is the entire reason "
        "projects and people are one file each. Use 'create'/'str_replace' only to FILE a "
        "genuinely new, durable fact in the ONE correct file per the routing rules in your "
        "memory protocol — a new person or project means 'create' on its own path, an active "
        "life state → current.md. Do not tidy other files; the Orion custodian owns hygiene. "
        "Every write is versioned. Most turns need no memory operations at all."
    )
    read_only = False
    # `view` never mutates and is safe to serve from the per-turn tool memo
    # like a fully read-only skill (registry.CapabilityRegistry._memoizable) —
    # every other command writes and must always run for real.
    memoizable_commands = frozenset({"view"})
    input_schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "enum": ["view", "create", "str_replace", "insert", "delete"],
                "description": (
                    "view: list directory or read file. "
                    "create: create new file. "
                    "str_replace: replace unique text in a file. "
                    "insert: insert text after a line number. "
                    "delete: delete a file."
                ),
            },
            "path": {
                "type": "string",
                "description": "File or directory path. Must start with /memories.",
            },
            "file_text": {
                "type": "string",
                "description": "File content for the create command.",
            },
            "old_str": {
                "type": "string",
                "description": "Exact text to replace (must be unique in the file).",
            },
            "new_str": {
                "type": "string",
                "description": "Replacement text.",
            },
            "insert_line": {
                "type": "integer",
                "description": "Line number to insert after (0 = before first line).",
            },
            "insert_text": {
                "type": "string",
                "description": "Text to insert.",
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
        path = args.get("path", "").rstrip("/")
        db = context.db
        user_id = context.user_id

        # Ensure initial files exist
        await ensure_seeded(user_id, db)

        err = _validate_path(path)
        if err:
            return err

        if command == "view":
            return await self._view(path, args, user_id, db)
        elif command == "create":
            return await self._create(path, args, context)
        elif command == "str_replace":
            return await self._str_replace(path, args, context)
        elif command == "insert":
            return await self._insert(path, args, context)
        elif command == "delete":
            return await self._delete(path, context)
        else:
            return f"Error: Unknown command '{command}'. Valid: view, create, str_replace, insert, delete."

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

        db.add(MemoryFile(
            user_id=user_id,
            path=path,
            content=content,
            updated_at=datetime.now(timezone.utc),
        ))
        await record_revision(
            db, user_id=user_id, path=path, author=context.agent_id,
            action="create", before="", after=content, request_id=context.request_id,
        )
        await db.commit()
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

        file.content = candidate
        file.updated_at = datetime.now(timezone.utc)
        await record_revision(
            db, user_id=user_id, path=path, author=context.agent_id,
            action="str_replace", before=before, after=file.content,
            request_id=context.request_id,
        )
        await db.commit()

        # Return snippet around the change
        snippet = _format_file_with_lines(path, file.content)
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

        file.content = candidate
        file.updated_at = datetime.now(timezone.utc)
        await record_revision(
            db, user_id=user_id, path=path, author=context.agent_id,
            action="insert", before=before, after=file.content,
            request_id=context.request_id,
        )
        await db.commit()
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

        # The canonical set is closed in BOTH directions: nothing may be added to
        # it and none of its members may be removed from it. Deleting one would
        # not lose the content — it is versioned — but it would leave the routing
        # tree pointing at a file that no longer exists, and the next agent with a
        # fact for it has nowhere legal to put it. `delete` exists for the tail of
        # a demotion: fold a STRAY file's content into the canonical file that
        # should have held it, then remove the stray.
        from app.services.memory_schema import is_canonical

        if is_canonical(path):
            return (
                f"Error: `{path}` is one of the canonical memory files and cannot be "
                f"deleted — the taxonomy is closed in both directions. To empty it, "
                f"demote its contents to the right file per the routing tree and leave "
                f"the file itself in place with its header."
            )

        before = file.content
        await db.execute(
            sql_delete(MemoryFile).where(
                MemoryFile.user_id == user_id,
                MemoryFile.path == path,
            )
        )
        await record_revision(
            db, user_id=user_id, path=path, author=context.agent_id,
            action="delete", before=before, after="", request_id=context.request_id,
        )
        await db.commit()
        logger.info("memory_file_deleted", extra={"user_id": user_id, "path": path})
        return f"Successfully deleted {path}"
