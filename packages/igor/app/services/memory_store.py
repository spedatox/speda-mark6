"""
Memory store service — the owner-facing write path and the shared revision trail.

Rule 1 (no logic in routers): the memory router calls into here; persistence,
optimistic-concurrency checks, the canonical-file guard, and the revision audit
all live in this service, never in the endpoint.

Three write paths feed one audit trail (MemoryRevision):
  - the memory SKILL (an agent writing mid-conversation)  → author = agent_id
  - Orion's nightly audit                                  → author = "orion"
  - the owner committing from the systems board            → author = "owner"

`record_revision` is the single choke point they all pass through.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memory_file import MemoryFile
from app.models.memory_revision import MemoryRevision

logger = logging.getLogger(__name__)

MEMORY_ROOT = "/memories"

# ── The canonical file taxonomy (docs/MEMORY_ARCHITECTURE.md §2) ──────────────
# The closed set of files the owner may view AND edit from the systems board.
# One question per file; anything outside this set is either system-internal
# (dot-prefixed, e.g. /memories/.audit/) or a stray that Orion folds back in.
# This list was written from docs/MEMORY_ARCHITECTURE.md's declared taxonomy and
# was wrong: the real store has fourteen files, and the five it did not know about
# were invisible to every gate, every migration and every audit. `wellness.md`
# alone is 25 KB of the owner's training data that no part of the system was
# protecting. The set below is the store as it actually is
# (docs/MEMORY_ARCHITECTURE_V4.md §2) — keep it that way, and add a file here the
# same day it is created.
CANONICAL_FILES: dict[str, str] = {
    # ── Shared, cross-agent ──────────────────────────────────────────────────
    "/memories/current.md": "What is true in the owner's life right now",
    "/memories/owner.md": "Who he is — biography in chapters, plus employment history",
    "/memories/dossier.md": "Observed preferences, behavioural prohibitions, trusted contacts",
    "/memories/history.md": "Mark VI-era states that have ended",
    "/memories/patterns.md": "Induced behavioural patterns, each with the move that pre-empts it",
    "/memories/social.md": "People, grouped by category — Who block + dated Events each",
    "/memories/projects.md": "Every project, with Status/Stack/Architecture/Team",
    "/memories/log.md": "Rolling one-line session summaries (system-written)",
    # ── Agent-owned domain files ─────────────────────────────────────────────
    # Ownership here is not aspirational: the revision trail shows academic.md
    # written by ultron 19 times and nobody else, wellness.md by atomix 11 of 12,
    # finance.md by sentinel 23 of 32. The store organised itself this way; this
    # records it so the gate can enforce it.
    "/memories/finance.md": "Monthly ledger — incomes, expenses, debts, schedules (Sentinel)",
    "/memories/wellness.md": "Training protocol, athlete profile and session log (Atomix)",
    "/memories/academic.md": "Academic calendar, KPSS prep, course materials, standing (Ultron)",
    "/memories/cybersec.md": "Cybersecurity learning journey (Centurion)",
    "/memories/ops.md": "Runbook and action log for the host (Orion)",
}

# Superseded by wellness.md, which is the same document continued — identical
# section structure (SYSTEM DIRECTIVES / ATHLETE PROFILE / PROGRAM / LOG) and
# newer. Kept out of the canonical set so the gate stops treating it as live, and
# removable through DELETE /memory/files once the owner is ready.
RETIRED_FILES: frozenset[str] = frozenset({
    "/memories/sessions.md",   # wellness.md is the same document continued
    "/memories/kpss.md",       # merged into academic.md as its KPSS 2026 section
})

# System-internal trails Orion writes but the owner does not edit. Dot-prefixed
# so they never enter the injection set or the canonical-file rule.
AUDIT_ROOT = "/memories/.audit"
# Documents whose members replaced them. Kept verbatim and readable by path,
# but never listed: offering the owner (or an agent) a 27 KB superseded copy
# beside the 4 KB member that supersedes it is an invitation to read the
# wrong one.
ARCHIVE_ROOT = "/memories/.archive"


class MemoryConflict(Exception):
    """Raised when an owner commit is stale — the file changed since it was read.

    Carries the current server-side copy so the caller can 409 with fresh state
    and let the systems board re-diff instead of clobbering an agent's write.
    """

    def __init__(self, current: MemoryFile):
        self.current = current
        super().__init__("memory file changed since it was read")


def is_owner_editable(path: str) -> bool:
    """Whether the board may edit this file's TEXT directly.

    Under v3 the answer is no for most of them, and that is the point: a derived
    file's text is regenerated from the record, so an edit to it would survive
    exactly until the next render and then vanish — silently, which is worse than
    refusing it. The owner still edits all of this knowledge; he edits the FACTS
    behind it (`/memory/observations`), which is the one place a correction
    sticks. See docs/MEMORY_ARCHITECTURE_V3.md §5.

    Only the system trails and any non-derived file remain text-editable.
    """
    from app.services.memory_compose import COMPOSED_FILES
    from app.services.memory_render import RENDERED_FILES

    if path.startswith(AUDIT_ROOT) or "/." in path:
        return False
    if path in RENDERED_FILES or path in COMPOSED_FILES:
        return False
    return path.startswith(MEMORY_ROOT + "/") and path.endswith(".md")


async def record_revision(
    db: AsyncSession,
    *,
    user_id: int,
    path: str,
    author: str,
    action: str,
    before: str,
    after: str,
    request_id: str = "",
) -> None:
    """Append one audit row for a memory mutation. Does NOT commit — the caller
    commits the file change and this row in the same transaction so the trail can
    never drift from the file it describes. Every write path routes through here."""
    db.add(
        MemoryRevision(
            user_id=user_id,
            path=path,
            author=author,
            action=action,
            before=before or "",
            after=after or "",
            request_id=request_id or "",
        )
    )


async def _get_file(db: AsyncSession, user_id: int, path: str) -> MemoryFile | None:
    result = await db.execute(
        select(MemoryFile).where(
            MemoryFile.user_id == user_id,
            MemoryFile.path == path,
        )
    )
    return result.scalar_one_or_none()


async def commit_file(
    db: AsyncSession,
    *,
    user_id: int,
    path: str,
    content: str,
    expected_updated_at: str | None,
    request_id: str = "",
) -> MemoryFile:
    """
    Owner commit from the systems board. Optimistic concurrency: if the file was
    written since the board loaded it (its `updated_at` no longer matches
    `expected_updated_at`), raise MemoryConflict instead of overwriting — an
    agent wrote mid-edit and the owner must re-diff. Records an `author="owner"`
    revision. Owner writes are ground truth (§4.3): Orion may re-file them but
    never alters their substance.
    """
    file = await _get_file(db, user_id, path)
    before = file.content if file else ""

    # Inspect against the file law but never block: §4.3 makes owner writes
    # ground truth. Violations are logged so Orion's audit picks them up and
    # re-files the content without altering a word of it. Imported lazily —
    # memory_schema imports this module for the canonical set.
    from app.services.memory_schema import check_write

    schema_notes = check_write(
        path=path, before=before, after=content,
        is_create=file is None, author="owner",
    )
    if schema_notes:
        logger.info(
            "memory_owner_commit_schema_notes",
            extra={"user_id": user_id, "path": path, "notes": schema_notes},
        )

    # Concurrency guard — only meaningful for an existing file.
    if file is not None and expected_updated_at is not None:
        current_stamp = file.updated_at.isoformat() if file.updated_at else None
        if current_stamp != expected_updated_at:
            raise MemoryConflict(file)

    if file is None:
        file = MemoryFile(user_id=user_id, path=path, content=content)
        db.add(file)
    else:
        file.content = content
        file.updated_at = datetime.now(timezone.utc)

    await record_revision(
        db,
        user_id=user_id,
        path=path,
        author="owner",
        action="commit",
        before=before,
        after=content,
        request_id=request_id,
    )
    await db.commit()
    await db.refresh(file)
    logger.info(
        "memory_owner_commit",
        extra={"user_id": user_id, "path": path, "request_id": request_id},
    )
    return file


async def delete_file(
    db: AsyncSession, *, user_id: int, path: str, request_id: str = ""
) -> str:
    """
    Remove a memory file, recording its full content in the revision trail first.

    The owner's only sanctioned way to retire a document. "Delete" here is a
    bookkeeping operation, not a destruction: the `before` of the recorded
    revision holds every byte, and `restore_revision` brings it back — which is
    why this is safe in a way that a hand-written DELETE against production
    would not be.

    Refuses canonical files. Retiring one of those is a schema change
    (`CANONICAL_FILES` and the routing that depends on it), not a click, and a
    file that agents are still told to write would simply be recreated empty.
    """
    from sqlalchemy import delete as sql_delete

    from app.models.memory_file import MemoryFile as _MF

    if path in CANONICAL_FILES:
        raise ValueError(
            f"{path} is a canonical memory file. Remove it from CANONICAL_FILES "
            f"first — otherwise agents are still routed to it and it comes back empty."
        )

    file = await _get_file(db, user_id, path)
    if file is None:
        raise KeyError(path)

    before = file.content
    await db.execute(
        sql_delete(_MF).where(_MF.user_id == user_id, _MF.path == path)
    )
    await record_revision(
        db, user_id=user_id, path=path, author="owner",
        action="delete", before=before, after="", request_id=request_id,
    )
    await db.commit()
    logger.info(
        "memory_file_deleted",
        extra={"user_id": user_id, "path": path, "bytes": len(before),
               "request_id": request_id},
    )
    return before


async def list_revisions(
    db: AsyncSession, user_id: int, path: str, limit: int = 50
) -> list[MemoryRevision]:
    """Newest-first revision history for one file — backs the per-file history
    list and one-click restore in the systems board."""
    result = await db.execute(
        select(MemoryRevision)
        .where(MemoryRevision.user_id == user_id, MemoryRevision.path == path)
        .order_by(MemoryRevision.created_at.desc(), MemoryRevision.id.desc())
        .limit(min(limit, 200))
    )
    return list(result.scalars().all())


async def restore_revision(
    db: AsyncSession, *, user_id: int, revision_id: int, request_id: str = ""
) -> MemoryFile:
    """Restore a file to the `after` content of an earlier revision. This is a
    NEW forward revision (author="owner", action="restore") — history is never
    rewritten, only appended to."""
    result = await db.execute(
        select(MemoryRevision).where(
            MemoryRevision.id == revision_id,
            MemoryRevision.user_id == user_id,
        )
    )
    rev = result.scalar_one_or_none()
    if rev is None:
        raise KeyError(f"No revision {revision_id} for this user")

    file = await _get_file(db, user_id, rev.path)
    before = file.content if file else ""
    target = rev.after

    if file is None:
        file = MemoryFile(user_id=user_id, path=rev.path, content=target)
        db.add(file)
    else:
        file.content = target
        file.updated_at = datetime.now(timezone.utc)

    await record_revision(
        db,
        user_id=user_id,
        path=rev.path,
        author="owner",
        action="restore",
        before=before,
        after=target,
        request_id=request_id,
    )
    await db.commit()
    await db.refresh(file)
    logger.info(
        "memory_revision_restored",
        extra={"user_id": user_id, "path": rev.path, "revision_id": revision_id},
    )
    return file
