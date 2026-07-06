"""
SPEDA Memory Skill — implements Anthropic's agent memory tool pattern.

Architecture (per Anthropic Memory Tool docs):
  - Memory is a virtual filesystem: structured markdown files under /memories/
  - SPEDA reads its memory directory at the start of every turn (JIT retrieval)
  - SPEDA writes and updates memory files when it learns something worth keeping
  - The agent controls its own memory — passive background extraction supplements this
    but the primary write path is SPEDA itself during conversations

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
**Standard address:** sir (EN) / Efendim (TR)

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
    "/memories/projects.md": """\
# Active Projects

## SPEDA Mark VI
Status: Active development (as of 2026-05)
Stack: FastAPI + Anthropic + PostgreSQL + Electron + React
Server: Contabo VPS
Description: Personal AI assistant — sixth iteration of the SPEDA series
""",
    "/memories/social.md": """\
# Social — people who matter to the owner

_One section per person important enough to track. Each has a **Who** block (who
they are and their context to the owner — updated in place as understanding
improves) and an append-only **Events** log, newest first. Facts about a PERSON
live here; the owner-side consequence of an event lives in current.md with a
cross-reference._

<!-- Schema — copy per person:
## <Person's name>
**Who:** who they are and their context to the owner (relation, role, standing facts).
**Events:**
- [YYYY-MM-DD] most recent thing concerning them (newest first).
-->
""",
    "/memories/sessions.md": """\
# Sessions — training log

_Gym log, day by day. Atomix is the only writer; other agents read. Program-level
facts ("6 days/week, cutting for the wedding") belong in current.md, not here.
Orion compresses entries older than ~4 weeks into weekly summaries._
""",
    "/memories/log.md": """\
# Session Log

(Rolling dated summary of recent sessions — most recent first)
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
PRELOAD_FILES = [
    "/memories/owner.md",
    "/memories/current.md",
    "/memories/dossier.md",
    "/memories/history.md",
]

# Per-agent extra preloads: an agent whose working file is one of the on-demand
# files gets it injected up front so it never has to spend a round-trip reading
# its own domain. Atomix owns the gym log (docs/MEMORY_ARCHITECTURE.md §2.1).
AGENT_EXTRA_PRELOAD: dict[str, list[str]] = {
    "atomix": ["/memories/sessions.md"],
}


# ── Path validation ───────────────────────────────────────────────────────────

def _validate_path(path: str) -> str | None:
    """Return error string if path is invalid, None if OK."""
    if not path.startswith(MEMORY_ROOT):
        return f"Error: Path must start with {MEMORY_ROOT}. Got: {path}"
    if ".." in path:
        return f"Error: Path traversal not allowed: {path}"
    return None


# ── Formatting helpers ────────────────────────────────────────────────────────

def _format_file_with_lines(path: str, content: str) -> str:
    lines = content.splitlines()
    numbered = "\n".join(f"{i + 1:6}\t{line}" for i, line in enumerate(lines))
    return f"Here's the content of {path} with line numbers:\n{numbered}"


def _format_directory(files: list[MemoryFile], path: str, *, with_sizes: bool = True) -> str:
    if not files:
        return f"Here are the files and directories up to 2 levels deep in {path}:\n(empty)"
    lines = [f"Here are the files and directories up to 2 levels deep in {path}:"]
    for f in sorted(files, key=lambda x: x.path):
        if with_sizes:
            size = len(f.content.encode("utf-8"))
            size_str = f"{size / 1024:.1f}K" if size >= 1024 else f"{size}B"
            lines.append(f"{size_str}\t{f.path}")
        else:
            # Size-free listing — stable across turns so the recall block stays
            # cacheable (file sizes change every turn as log.md grows, which would
            # otherwise bust the prompt cache on every request).
            lines.append(f.path)
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

# In-process recall cache: the assembled memory block is stable within a
# session (memory rarely changes mid-conversation). Cache the result keyed on
# the max updated_at timestamp — if no memory file was written since the last
# recall, skip the full assembly and return the cached string. Saves a DB
# round-trip + string assembly on every turn after the first.
_recall_cache: dict[tuple[int, str], tuple[str, str]] = {}  # (user_id, agent_id) -> (watermark, block)


async def recall_for_context(user_id: int, db, agent_id: str = "speda") -> str:
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

    # Watermark: if no file changed since last recall, return the cached block.
    # Keyed by (user_id, agent_id) — different agents preload different files.
    watermark = max((f.updated_at.isoformat() for f in all_files), default="")
    cache_key = (user_id, agent_id)
    cached = _recall_cache.get(cache_key)
    if cached and cached[0] == watermark:
        return cached[1]

    by_path = {f.path: f for f in all_files}

    # Size-free listing keeps this recall block byte-stable across turns so the
    # prompt cache holds (file sizes otherwise change every turn as log.md grows).
    listing = _format_directory(all_files, MEMORY_ROOT, with_sizes=False)

    preload = PRELOAD_FILES + AGENT_EXTRA_PRELOAD.get(agent_id, [])
    sections = [f"### Directory\n\n{listing}"]
    for path in preload:
        f = by_path.get(path)
        if f:
            sections.append(f"### {path}\n\n{f.content.strip()}")

    body = "\n\n".join(sections)
    block = (
        "## Memory\n\n"
        "This is shared knowledge about your OWNER, maintained across all of your "
        "sessions. It describes HIM — his profile, what is current for him, and how "
        "he likes to be treated. It does NOT define who you are: your own identity, "
        "name and role are set above and are unaffected by anything in this section. "
        "Read it as notes about the owner, never as a description of yourself.\n\n"
        f"{body}\n\n"
        "Use the `memory` tool to read other files (projects.md, social.md, "
        "sessions.md, log.md) or to update memory during this session. dossier.md "
        "shapes how you respond — act on it, never cite it aloud."
    )
    _recall_cache[cache_key] = (watermark, block)
    return block


# ── The skill ─────────────────────────────────────────────────────────────────

class MemorySkill(Skill):
    """
    SPEDA's persistent memory tool.
    Implements Anthropic's agent memory pattern: view/create/str_replace/insert/delete.
    SPEDA uses this to maintain continuity across sessions without reloading
    everything into the context window upfront.
    """

    name = "memory"
    description = (
        "Read or write the owner's persistent memory files under /memories. "
        "owner.md, current.md, dossier.md and history.md are ALREADY in your context every "
        "turn — never use this tool to read them. Use 'view' only to open a SPECIFIC other "
        "file (projects.md, social.md, sessions.md, log.md) when the task needs detail you "
        "don't already have. Use 'create'/'str_replace' only to FILE a genuinely new, durable "
        "fact in the ONE correct file per the routing rules in your memory protocol — a person "
        "→ social.md, a project's progress → projects.md, an active life state → current.md. "
        "Do not tidy other files; the Orion custodian owns hygiene. Every write is versioned. "
        "Most turns need no memory operations at all."
    )
    read_only = False
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
            result = await db.execute(
                select(MemoryFile).where(
                    MemoryFile.user_id == user_id,
                    MemoryFile.path.startswith(path),
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
        return f"File created successfully at: {path}"

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
        file.content = file.content.replace(old_str, new_str, 1)
        file.updated_at = datetime.now(timezone.utc)
        await record_revision(
            db, user_id=user_id, path=path, author=context.agent_id,
            action="str_replace", before=before, after=file.content,
            request_id=context.request_id,
        )
        await db.commit()

        # Return snippet around the change
        snippet = _format_file_with_lines(path, file.content)
        return f"The memory file has been edited.\n{snippet}"

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
        file.content = "\n".join(lines)
        file.updated_at = datetime.now(timezone.utc)
        await record_revision(
            db, user_id=user_id, path=path, author=context.agent_id,
            action="insert", before=before, after=file.content,
            request_id=context.request_id,
        )
        await db.commit()
        return f"The file {path} has been edited."

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
