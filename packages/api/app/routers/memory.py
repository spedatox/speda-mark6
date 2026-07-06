import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.memory_file import MemoryFile
from app.services import memory_store

logger = logging.getLogger(__name__)
router = APIRouter(tags=["memory"])

# Single-user system — user 1, matching the rest of the backend.
_USER_ID = 1


class MemoryCommit(BaseModel):
    path: str
    content: str
    # ISO timestamp the board last saw for this file. Optimistic concurrency:
    # a mismatch means an agent wrote since, and we 409 instead of clobbering.
    expected_updated_at: str | None = None


class RevisionRestore(BaseModel):
    revision_id: int


def _serialize(f: MemoryFile) -> dict:
    return {
        "path": f.path,
        "content": f.content or "",
        "updated_at": f.updated_at.isoformat() if f.updated_at else None,
        "editable": memory_store.is_owner_editable(f.path),
    }


@router.get("/memory/files")
async def list_memory_files(db: AsyncSession = Depends(get_db)):
    """
    SPEDA's knowledge bank — the /memories virtual filesystem. Backs the
    DATA_BANKS // KNOWLEDGE panel. Canonical files are flagged `editable`; the
    dot-prefixed system trails (`.audit/…`) are hidden entirely — the owner edits
    memory, not the audit log.
    """
    result = await db.execute(
        select(MemoryFile)
        .where(MemoryFile.user_id == _USER_ID)
        .order_by(MemoryFile.path)
    )
    files = result.scalars().all()
    return [
        _serialize(f)
        for f in files
        if not f.path.startswith(memory_store.AUDIT_ROOT)
    ]


@router.put("/memory/files")
async def commit_memory_file(body: MemoryCommit, db: AsyncSession = Depends(get_db)):
    """
    Owner commit from the systems board. Only canonical files are editable; the
    write is version-stamped into the revision trail (author="owner") and guarded
    by optimistic concurrency — a stale `expected_updated_at` returns 409 with the
    fresh server copy so the board can re-diff.
    """
    if not memory_store.is_owner_editable(body.path):
        raise HTTPException(
            status_code=400,
            detail=f"'{body.path}' is not an owner-editable memory file.",
        )
    request_id = str(uuid.uuid4())
    try:
        file = await memory_store.commit_file(
            db,
            user_id=_USER_ID,
            path=body.path,
            content=body.content,
            expected_updated_at=body.expected_updated_at,
            request_id=request_id,
        )
    except memory_store.MemoryConflict as conflict:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "This file changed since you loaded it. Re-review before saving.",
                "current": _serialize(conflict.current),
            },
        )
    return _serialize(file)


@router.get("/memory/files/revisions")
async def memory_revisions(path: str, db: AsyncSession = Depends(get_db)):
    """Newest-first revision history for one file — feeds the per-file history
    list and one-click restore in the systems board."""
    revs = await memory_store.list_revisions(db, _USER_ID, path)
    return [
        {
            "id": r.id,
            "path": r.path,
            "author": r.author,
            "action": r.action,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "before": r.before,
            "after": r.after,
        }
        for r in revs
    ]


@router.post("/memory/files/restore")
async def restore_memory_file(body: RevisionRestore, db: AsyncSession = Depends(get_db)):
    """Restore a file to an earlier revision's content. This is a NEW forward
    revision (author='owner'), never a rewrite of history."""
    request_id = str(uuid.uuid4())
    try:
        file = await memory_store.restore_revision(
            db, user_id=_USER_ID, revision_id=body.revision_id, request_id=request_id
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="No such revision.")
    return _serialize(file)
