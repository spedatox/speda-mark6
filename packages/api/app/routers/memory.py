import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.memory_file import MemoryFile
from app.services import memory_store
from app.skills.memory import AGENT_SOURCE_DEFAULTS, source_file_for

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


class SourceAssign(BaseModel):
    agent_id: str
    # /memories/*.md to assign, or null to clear (revert to the built-in default).
    path: str | None = None


@router.get("/memory/sources")
async def get_memory_sources(request: Request, db: AsyncSession = Depends(get_db)):
    """Per-agent source-of-truth assignments + the pool of files to choose from.
    Backs the Configuration tab's 'Agent Source of Truth' picker: each agent's
    domain file is preloaded into its prompt and is where it writes its data."""
    result = await db.execute(
        select(MemoryFile.path)
        .where(MemoryFile.user_id == _USER_ID)
        .order_by(MemoryFile.path)
    )
    files = [
        p for (p,) in result.all()
        if memory_store.is_owner_editable(p)
    ]
    profiles = request.app.state.profiles
    agents = [
        {
            "agent_id": p.agent_id,
            "name": p.name,
            "domain": p.domain,
            "source": source_file_for(p.agent_id),
            "default": AGENT_SOURCE_DEFAULTS.get(p.agent_id),
        }
        for p in profiles.roster()
        if p.dispatch_target  # skip session-scope aliases (warroom)
    ]
    return {"files": files, "agents": agents}


@router.put("/memory/sources")
async def set_memory_source(body: SourceAssign, request: Request, db: AsyncSession = Depends(get_db)):
    """Assign (or clear) an agent's source-of-truth file. Validates the agent and
    the path, creates the file with a header if it doesn't exist yet, and persists
    the mapping (runtime_state) so it survives restarts and takes effect next turn."""
    from app.core.runtime_state import set_agent_source

    if request.app.state.profiles.get(body.agent_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown agent '{body.agent_id}'")

    path = (body.path or "").strip() or None
    if path is not None:
        if not memory_store.is_owner_editable(path):
            raise HTTPException(
                status_code=400,
                detail=f"'{path}' is not a valid /memories/*.md file.",
            )
        # Create the file if the owner picked a name that doesn't exist yet.
        existing = await db.execute(
            select(MemoryFile).where(MemoryFile.user_id == _USER_ID, MemoryFile.path == path)
        )
        if existing.scalar_one_or_none() is None:
            name = path.rsplit("/", 1)[-1].removesuffix(".md")
            db.add(MemoryFile(
                user_id=_USER_ID,
                path=path,
                content=f"# {name.title()}\n\n_Source-of-truth file. Managed by the assigned agent._\n",
                updated_at=datetime.now(timezone.utc),
            ))
            await db.commit()

    set_agent_source(body.agent_id, path)
    return {"agent_id": body.agent_id, "source": source_file_for(body.agent_id)}


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
