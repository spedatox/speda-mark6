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
from app.skills.base import Skill

logger = logging.getLogger(__name__)

MEMORY_ROOT = "/memories"

# ── Initial file templates seeded on first use ────────────────────────────────

INITIAL_FILES = {
    "/memories/owner.md": """\
# Owner Profile

**Name:** Ahmet Erol Bayrak
**Codename:** Spedatox
**Standard address:** sir (EN) / Efendim (TR)

## Communication style
- Direct, dry, occasionally sardonic
- No padding, no "Certainly!", no unnecessary hedging
- Prefers concise, actionable responses
- JARVIS register, not Siri
""",
    "/memories/projects.md": """\
# Active Projects

## SPEDA Mark VI
Status: Active development
Stack: FastAPI + Anthropic + PostgreSQL + Electron + React
Server: Contabo VPS
Description: Personal AI assistant — sixth iteration of the SPEDA series
""",
    "/memories/preferences.md": """\
# Explicit Instructions & Preferences

(SPEDA updates this file when the owner gives explicit instructions or preferences)
""",
    "/memories/log.md": """\
# Session Log

(Rolling summary of recent sessions — most recent first)
""",
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


def _format_directory(files: list[MemoryFile], path: str) -> str:
    if not files:
        return f"Here are the files and directories up to 2 levels deep in {path}:\n(empty)"
    lines = [f"Here are the files and directories up to 2 levels deep in {path}:"]
    for f in sorted(files, key=lambda x: x.path):
        size = len(f.content.encode("utf-8"))
        size_str = f"{size / 1024:.1f}K" if size >= 1024 else f"{size}B"
        lines.append(f"{size_str}\t{f.path}")
    return "\n".join(lines)


# ── Seed helpers ──────────────────────────────────────────────────────────────

async def seed_initial_files(user_id: int, db) -> None:
    """Create the default memory files for a user who has none yet."""
    for path, content in INITIAL_FILES.items():
        existing = await db.execute(
            select(MemoryFile).where(
                MemoryFile.user_id == user_id,
                MemoryFile.path == path,
            )
        )
        if existing.scalar_one_or_none() is None:
            db.add(MemoryFile(user_id=user_id, path=path, content=content))
    await db.commit()
    logger.info("memory_files_seeded", extra={"user_id": user_id})


async def ensure_seeded(user_id: int, db) -> None:
    """Seed initial files if the user has no memory files yet."""
    result = await db.execute(
        select(MemoryFile).where(MemoryFile.user_id == user_id).limit(1)
    )
    if result.scalar_one_or_none() is None:
        await seed_initial_files(user_id, db)


# ── Recall for context injection (used by orchestrator) ──────────────────────

async def recall_for_context(user_id: int, db) -> str:
    """
    Load the memory context to prepend to the system prompt.
    Returns: directory listing (so SPEDA knows what exists) + owner.md contents.
    SPEDA reads additional files JIT during the conversation via the memory tool.
    """
    await ensure_seeded(user_id, db)

    # Directory listing
    result = await db.execute(
        select(MemoryFile).where(MemoryFile.user_id == user_id)
    )
    all_files = result.scalars().all()
    listing = _format_directory(list(all_files), MEMORY_ROOT)

    # Always preload owner.md — it's always relevant
    owner_result = await db.execute(
        select(MemoryFile).where(
            MemoryFile.user_id == user_id,
            MemoryFile.path == "/memories/owner.md",
        )
    )
    owner_file = owner_result.scalar_one_or_none()
    owner_content = owner_file.content if owner_file else ""

    block = (
        "## Memory\n\n"
        f"### Directory\n\n{listing}\n\n"
        f"### {MEMORY_ROOT}/owner.md\n\n{owner_content}\n\n"
        "Use the `memory` tool to read other files or update memory during this session."
    )
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
        "Read and write persistent memory files that survive across conversations. "
        "ALWAYS view /memories at the start of a new task to check for relevant context. "
        "Use str_replace to update existing facts rather than appending duplicates. "
        "Keep files organised and up-to-date — delete or rename stale files."
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
            return await self._create(path, args, user_id, db)
        elif command == "str_replace":
            return await self._str_replace(path, args, user_id, db)
        elif command == "insert":
            return await self._insert(path, args, user_id, db)
        elif command == "delete":
            return await self._delete(path, user_id, db)
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

    async def _create(self, path: str, args: dict, user_id: int, db) -> str:
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
        await db.commit()
        logger.info("memory_file_created", extra={"user_id": user_id, "path": path})
        return f"File created successfully at: {path}"

    async def _str_replace(self, path: str, args: dict, user_id: int, db) -> str:
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

        file.content = file.content.replace(old_str, new_str, 1)
        file.updated_at = datetime.now(timezone.utc)
        await db.commit()

        # Return snippet around the change
        snippet = _format_file_with_lines(path, file.content)
        return f"The memory file has been edited.\n{snippet}"

    async def _insert(self, path: str, args: dict, user_id: int, db) -> str:
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

        lines.insert(insert_line, insert_text.rstrip("\n"))
        file.content = "\n".join(lines)
        file.updated_at = datetime.now(timezone.utc)
        await db.commit()
        return f"The file {path} has been edited."

    async def _delete(self, path: str, user_id: int, db) -> str:
        result = await db.execute(
            select(MemoryFile).where(
                MemoryFile.user_id == user_id,
                MemoryFile.path == path,
            )
        )
        file = result.scalar_one_or_none()
        if file is None:
            return f"Error: The path {path} does not exist."

        await db.execute(
            sql_delete(MemoryFile).where(
                MemoryFile.user_id == user_id,
                MemoryFile.path == path,
            )
        )
        await db.commit()
        logger.info("memory_file_deleted", extra={"user_id": user_id, "path": path})
        return f"Successfully deleted {path}"
