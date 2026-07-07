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
CANONICAL_FILES: dict[str, str] = {
    "/memories/current.md": "What is true in the owner's life right now",
    "/memories/owner.md": "Who he is and his biography before Mark VI existed",
    "/memories/dossier.md": "Observed preferences — what he likes, dislikes, wants and how",
    "/memories/projects.md": "What he is building and where each effort stands",
    "/memories/social.md": "Who matters to him — Who block + timestamped Events per person",
    "/memories/sessions.md": "Gym log, day by day (Atomix's domain)",
    "/memories/history.md": "Mark VI-era states that have ended (demotions only)",
    "/memories/log.md": "Rolling one-line session summaries",
}

# System-internal trails Orion writes but the owner does not edit. Dot-prefixed
# so they never enter the injection set or the canonical-file rule.
AUDIT_ROOT = "/memories/.audit"


class MemoryConflict(Exception):
    """Raised when an owner commit is stale — the file changed since it was read.

    Carries the current server-side copy so the caller can 409 with fresh state
    and let the systems board re-diff instead of clobbering an agent's write.
    """

    def __init__(self, current: MemoryFile):
        self.current = current
        super().__init__("memory file changed since it was read")


def is_owner_editable(path: str) -> bool:
    """Any markdown file under /memories is owner-editable from the board — the
    canonical set PLUS any file the owner or an agent adds later (e.g.
    finance.md). The only exclusions are the dot-prefixed system trails
    (`.audit/…`), which Orion writes and the owner does not touch."""
    if path.startswith(AUDIT_ROOT) or "/." in path:
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
