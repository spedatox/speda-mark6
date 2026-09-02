# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

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
    Speda's knowledge bank — the /memories virtual filesystem. Backs the
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
        and not f.path.startswith(memory_store.ARCHIVE_ROOT)
    ]


@router.get("/memory/folders")
async def list_memory_folders():
    """Every folder the store DECLARES, whether or not it holds a file yet.

    A folder with no files does not exist in the table — memory files are rows,
    not a filesystem — so `life/` was invisible in the knowledge bank until the
    first document landed in it. That is backwards: the owner should be able to
    see where a thing WILL go before anything has gone there, and an agent is
    already told the folder exists. Serves the declaration, not the data.
    """
    from app.services.memory_spec import COLLECTIONS

    out = [
        {"path": c.root, "summary": c.summary, "owner_agent": c.owner_agent,
         "open": not c.closed}
        for c in COLLECTIONS
    ]
    # A SHARDED member is a folder as well, and one whose files are open-ended:
    # a new month is a new file nobody declares in advance. Declaring the folder
    # is what lets the board name it for what it holds ("one file per month")
    # instead of leaving the owner to infer it from whichever months exist.
    out += [
        {"path": f"{c.root}/{m.stem}", "summary": m.summary or m.title,
         "owner_agent": c.owner_agent, "open": True}
        for c in COLLECTIONS for m in c.members if m.shard
    ]
    return out


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
    from app.services.memory_spec import COLLECTIONS

    # A source of truth is a DIRECTORY now — an agent's domain is a folder of
    # topics, and its standing members are what gets preloaded. The pool has to
    # offer those roots or the picker can only assign paths the resolver no
    # longer understands, which is exactly how four agents ended up pinned to
    # documents that had been archived. Loose .md files stay offered: a domain
    # that was never split is still one file, and the owner may pin one.
    files = [c.root for c in COLLECTIONS] + [
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

    from app.services.memory_spec import collection_by_root

    path = (body.path or "").strip() or None
    # A collection root is assignable and needs no file created — it IS the
    # domain, and its members already exist.
    if path is not None and collection_by_root(path) is not None:
        set_agent_source(body.agent_id, path)
        return {"agent_id": body.agent_id, "source": source_file_for(body.agent_id)}
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


@router.delete("/memory/files")
async def delete_memory_file(path: str, db: AsyncSession = Depends(get_db)):
    """
    Retire a memory file the owner no longer wants.

    Reversible: the file's full content is written to the revision trail before
    it goes, so `POST /memory/files/restore` brings it back byte for byte.
    Canonical files are refused — retiring one is a schema change, and a file
    agents are still routed to would just be recreated empty.
    """
    request_id = str(uuid.uuid4())
    try:
        content = await memory_store.delete_file(
            db, user_id=_USER_ID, path=path, request_id=request_id
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"No such memory file: {path}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"deleted": path, "bytes": len(content), "recoverable": True}


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


# ── The record itself (v3 §5) ─────────────────────────────────────────────────
# The owner edits FACTS, not markdown. Six of the eight files are rendered and
# two are composed, so an edit to their text would be overwritten on the next
# render — silently. These endpoints are where a correction actually sticks, and
# they are what the DATA_BANKS panel drives instead of a textarea.


class ObservationIn(BaseModel):
    content: str
    domain: str
    subject: str = "owner"
    valid_from: str | None = None
    valid_until: str | None = None
    supersedes: int | None = None


class ObservationEnd(BaseModel):
    valid_until: str | None = None


def _serialize_observation(o) -> dict:
    return {
        "id": o.id,
        "content": o.content,
        "subject": o.subject,
        "domain": o.domain,
        "level": o.level,
        "observer": o.observer,
        "origin": o.origin,
        "valid_from": o.valid_from.isoformat() if o.valid_from else None,
        "valid_until": o.valid_until.isoformat() if o.valid_until else None,
        "superseded_by": o.superseded_by,
        "reinforcement_count": o.reinforcement_count,
        "source_ids": list(o.source_ids or []),
        "session_id": o.session_id,
        "created_at": o.created_at.isoformat() if o.created_at else None,
        # Where this fact shows up — so the panel can group by surface and the
        # owner sees the same organisation he reads in the files.
        "surface": _target_file(o),
    }


def _target_file(o) -> str:
    from app.services.observations import target_file

    return target_file(o)


@router.get("/memory/observations")
async def list_observations(
    subject: str | None = None,
    domain: str | None = None,
    surface: str | None = None,
    live_only: bool = False,
    limit: int = 500,
    db: AsyncSession = Depends(get_db),
):
    """
    The record behind the files — what the roster believes about the owner.

    Filterable by subject, domain, or the surface a fact renders into, so the
    panel can show "everything behind dossier.md" and let the owner correct it at
    the source. Ended facts are included by default and carry their end date;
    hiding them would recreate the v2 problem of history being invisible.
    """
    from app.models.observation import Observation
    from app.services.observations import normalize_subject

    stmt = select(Observation).where(
        Observation.user_id == _USER_ID, Observation.deleted_at.is_(None)
    )
    if subject:
        stmt = stmt.where(Observation.subject == normalize_subject(subject))
    if domain:
        stmt = stmt.where(Observation.domain == domain)
    if live_only:
        stmt = stmt.where(Observation.valid_until.is_(None))
    stmt = stmt.order_by(Observation.created_at.desc()).limit(min(limit, 2000))

    rows = (await db.execute(stmt)).scalars().all()
    out = [_serialize_observation(o) for o in rows]
    if surface:
        out = [o for o in out if o["surface"] == surface]
    return out


@router.post("/memory/observations")
async def create_observation(body: ObservationIn, db: AsyncSession = Depends(get_db)):
    """
    Record a fact as the owner. `observer="owner"`, `origin="owner"` — ground
    truth (§4.3): it outranks agent observations and no re-index ever regenerates
    or removes it.

    Pass `supersedes` to correct an existing fact rather than contradict it: the
    old row is closed out with an end date and a pointer here, so the previous
    value stays answerable and the correction stays reversible.
    """
    from app.services.observations import ObservationRejected, record_observations, supersede

    request_id = str(uuid.uuid4())
    try:
        stored, rejections = await record_observations(
            db,
            user_id=_USER_ID,
            observer="owner",
            proposals=[body.model_dump(exclude={"supersedes"}) | {"level": "explicit"}],
            request_id=request_id,
            origin="owner",
        )
    except ObservationRejected as e:
        raise HTTPException(status_code=400, detail=str(e))
    if rejections:
        raise HTTPException(status_code=400, detail=rejections[0])
    if not stored:
        raise HTTPException(status_code=400, detail="Nothing was recorded.")

    if body.supersedes:
        await supersede(
            db, user_id=_USER_ID, old_id=body.supersedes, new_id=stored[0].id
        )
    logger.info(
        "owner_observation_recorded",
        extra={"request_id": request_id, "id": stored[0].id},
    )
    return _serialize_observation(stored[0])


@router.post("/memory/observations/{observation_id}/end")
async def end_observation(
    observation_id: int, body: ObservationEnd, db: AsyncSession = Depends(get_db)
):
    """
    Mark a fact as no longer true, without replacing it.

    This is how something leaves the present tense: current.md stops showing it
    and history.md starts, with no text moved anywhere. Distinct from deleting —
    the fact remains true of the past, which is exactly what history is for.
    """
    from datetime import date as _date

    from app.models.observation import Observation
    from app.services.observations import _parse_day

    obs = (
        await db.execute(
            select(Observation).where(
                Observation.id == observation_id,
                Observation.user_id == _USER_ID,
                Observation.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if obs is None:
        raise HTTPException(status_code=404, detail="No such observation.")
    if obs.domain == "biography":
        raise HTTPException(
            status_code=400,
            detail=(
                "A biography fact cannot be ended — the past does not expire. "
                "If it was wrong, delete it; if it described a state, correct the "
                "domain."
            ),
        )
    obs.valid_until = _parse_day(body.valid_until, "valid_until") or _date.today()
    obs.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return _serialize_observation(obs)


@router.delete("/memory/observations/{observation_id}")
async def delete_observation(observation_id: int, db: AsyncSession = Depends(get_db)):
    """
    Demote a fact out of the record — for something that was never true, not for
    something that stopped being true (use /end for that).

    Soft, per §3.4: the row stays readable in the audit trail and only leaves
    recall and the rendered surfaces.
    """
    from app.services.observations import soft_delete_observations

    count = await soft_delete_observations(
        db, user_id=_USER_ID, observation_ids=[observation_id]
    )
    if not count:
        raise HTTPException(status_code=404, detail="No such live observation.")
    return {"demoted": count}
