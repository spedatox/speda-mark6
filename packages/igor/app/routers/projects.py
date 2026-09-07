# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Projects — CRUD for the workspace surface every client renders.

Isolation contract, stated once and enforced on every route here: a project
belongs to exactly one agent. Every listing is filtered by `agent_id`, and every
route that names a project by id passes it through `_owned()`, which 404s when
the project belongs to someone else. There is no route that returns a project
without an agent to check it against — that absence is the point, because a
"just fetch it by id" endpoint is how one agent's knowledge base ends up in
another agent's prompt.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.project import Project, ProjectFile
from app.models.session import Session
from app.schemas.project import ProjectCreate, ProjectFileUpload, ProjectUpdate

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/projects", tags=["projects"])

USER_ID = 1  # single-owner system, like every other router here


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _owned(db: AsyncSession, project_id: int, agent_id: str) -> Project:
    """The project, or 404. The agent check is the isolation boundary — a
    project addressed by an agent that does not own it does not exist as far as
    this API is concerned, which is deliberately indistinguishable from a bad
    id: a client has no business learning that another agent has a project N."""
    project = (
        await db.execute(
            select(Project).where(Project.id == project_id, Project.user_id == USER_ID)
        )
    ).scalar_one_or_none()
    if project is None or project.agent_id != agent_id:
        raise HTTPException(status_code=404, detail=f"No project {project_id} for '{agent_id}'")
    return project


def _serialize(p: Project, chats: int = 0, files: int = 0) -> dict:
    return {
        "id": p.id,
        "agent_id": p.agent_id,
        "name": p.name,
        "description": p.description or "",
        "instructions": p.instructions or "",
        "icon": p.icon or "",
        "color": p.color or "",
        "pinned": bool(p.pinned),
        "archived": p.archived_at is not None,
        "created_at": p.created_at.isoformat(),
        "updated_at": p.updated_at.isoformat(),
        "last_activity_at": p.last_activity_at.isoformat(),
        "chat_count": chats,
        "file_count": files,
    }


@router.get("")
async def list_projects(
    agent_id: str = "speda",
    include_archived: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """This agent's projects, pinned first then newest activity. Never any
    other agent's — see the module docstring."""
    stmt = select(Project).where(Project.user_id == USER_ID, Project.agent_id == agent_id)
    if not include_archived:
        stmt = stmt.where(Project.archived_at.is_(None))
    projects = list(
        (
            await db.execute(
                stmt.order_by(Project.pinned.desc(), Project.last_activity_at.desc())
            )
        ).scalars().all()
    )
    if not projects:
        return []

    ids = [p.id for p in projects]
    # Two grouped counts rather than 2N queries — the grid shows both numbers on
    # every card, so they are part of the listing, not a per-card fetch.
    chat_rows = await db.execute(
        select(Session.project_id, func.count(Session.id))
        .where(Session.project_id.in_(ids))
        .group_by(Session.project_id)
    )
    chats = dict(chat_rows.all())
    file_rows = await db.execute(
        select(ProjectFile.project_id, func.count(ProjectFile.id))
        .where(ProjectFile.project_id.in_(ids))
        .group_by(ProjectFile.project_id)
    )
    files = dict(file_rows.all())
    return [_serialize(p, chats.get(p.id, 0), files.get(p.id, 0)) for p in projects]


@router.post("")
async def create_project(
    body: ProjectCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    if not settings.projects_enabled:
        raise HTTPException(status_code=403, detail="Projects are disabled")
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="A project needs a name")
    # An unknown agent would create a workspace nothing can ever reach, so it is
    # a routing error here exactly as it is in the chat router.
    if request.app.state.profiles.get(body.agent_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown agent '{body.agent_id}'")

    instructions = (body.instructions or "").strip()[: settings.projects_instructions_max_chars]
    project = Project(
        user_id=USER_ID,
        agent_id=body.agent_id,
        name=name[:200],
        description=(body.description or "").strip() or None,
        instructions=instructions or None,
        icon=(body.icon or "").strip() or None,
        color=(body.color or "").strip() or None,
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)
    logger.info("project_created", extra={"project_id": project.id, "agent_id": project.agent_id})
    return _serialize(project)


@router.get("/{project_id}")
async def get_project(
    project_id: int,
    agent_id: str = "speda",
    db: AsyncSession = Depends(get_db),
):
    project = await _owned(db, project_id, agent_id)
    chats = (
        await db.execute(
            select(func.count(Session.id)).where(Session.project_id == project.id)
        )
    ).scalar() or 0
    files = (
        await db.execute(
            select(func.count(ProjectFile.id)).where(ProjectFile.project_id == project.id)
        )
    ).scalar() or 0
    return _serialize(project, chats, files)


@router.patch("/{project_id}")
async def update_project(
    project_id: int,
    body: ProjectUpdate,
    agent_id: str = "speda",
    db: AsyncSession = Depends(get_db),
):
    project = await _owned(db, project_id, agent_id)

    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="A project needs a name")
        project.name = name[:200]
    if body.description is not None:
        project.description = body.description.strip() or None
    if body.instructions is not None:
        # Truncated rather than refused: the owner edits this in a textarea, and
        # losing a long edit to a 400 is worse than losing its tail to the cap.
        project.instructions = (
            body.instructions.strip()[: settings.projects_instructions_max_chars] or None
        )
    if body.icon is not None:
        project.icon = body.icon.strip()[:16] or None
    if body.color is not None:
        project.color = body.color.strip()[:16] or None
    if body.pinned is not None:
        project.pinned = body.pinned
    if body.archived is not None:
        project.archived_at = _now() if body.archived else None

    project.updated_at = _now()
    await db.commit()
    await db.refresh(project)
    return _serialize(project)


@router.delete("/{project_id}")
async def delete_project(
    project_id: int,
    agent_id: str = "speda",
    db: AsyncSession = Depends(get_db),
):
    """Delete the project and its knowledge base. The CHATS survive, detached.

    Same reasoning as deleting a session leaving its memories standing: a
    conversation is a record of something that was actually said, and it does
    not stop having happened because the folder it was filed in is gone. The
    chats reappear in the main history list, unfiled. Archive (PATCH
    archived=true) is the reversible option and is what the UI offers first."""
    project = await _owned(db, project_id, agent_id)
    await db.execute(
        update(Session).where(Session.project_id == project.id).values(project_id=None)
    )
    await db.execute(delete(ProjectFile).where(ProjectFile.project_id == project.id))
    await db.execute(delete(Project).where(Project.id == project.id))
    await db.commit()
    logger.info("project_deleted", extra={"project_id": project_id, "agent_id": agent_id})
    return {"ok": True}


@router.get("/{project_id}/sessions")
async def list_project_sessions(
    project_id: int,
    request: Request,
    agent_id: str = "speda",
    limit: int = 500,
    db: AsyncSession = Depends(get_db),
):
    """This project's chats, newest first. Ownership is checked first, so this
    can never be used to read another agent's conversation list."""
    await _owned(db, project_id, agent_id)
    session_manager = request.app.state.session_manager
    sessions = await session_manager.list_sessions(
        db, user_id=USER_ID, agent_id=agent_id, limit=limit, project_id=project_id
    )
    return [
        {
            "id": s.id,
            "title": s.title,
            "started_at": s.started_at.isoformat(),
            "project_id": s.project_id,
            "tokens_in": s.token_count_input or 0,
            "tokens_out": s.token_count_output or 0,
        }
        for s in sessions
    ]


@router.get("/{project_id}/files")
async def list_project_files(
    project_id: int,
    agent_id: str = "speda",
    db: AsyncSession = Depends(get_db),
):
    await _owned(db, project_id, agent_id)
    files = (
        await db.execute(
            select(ProjectFile)
            .where(ProjectFile.project_id == project_id)
            .order_by(ProjectFile.created_at.desc())
        )
    ).scalars().all()
    # The extracted TEXT is deliberately not in the listing — a knowledge base
    # can run to hundreds of KB and the panel only draws chips.
    return [
        {
            "id": f.id,
            "name": f.name,
            "media_type": f.media_type,
            "size": f.size_bytes,
            "chars": f.chars,
            "created_at": f.created_at.isoformat(),
        }
        for f in files
    ]


@router.post("/{project_id}/files")
async def add_project_file(
    project_id: int,
    body: ProjectFileUpload,
    agent_id: str = "speda",
    db: AsyncSession = Depends(get_db),
):
    """Extract an upload to text and file it in the project's knowledge base.

    Extraction is the same server-side path chat attachments take
    (services/attachments.extract_body), for the same reason: text is the one
    representation that reaches all six providers identically.

    Where the two paths diverge is failure. A chat attachment degrades to a note
    — a failed turn is worse than a turn that knows a file was unreadable. A
    knowledge file must not: the note would be STORED, and every later turn in
    the project would read "could not be read" as a fact about the subject. So a
    file whose text cannot be recovered is refused here, with the extractor's own
    reason, and never reaches the knowledge base."""
    project = await _owned(db, project_id, agent_id)

    count = (
        await db.execute(
            select(func.count(ProjectFile.id)).where(ProjectFile.project_id == project.id)
        )
    ).scalar() or 0
    if count >= settings.projects_max_files:
        raise HTTPException(
            status_code=400,
            detail=f"This project already holds its limit of {settings.projects_max_files} files",
        )

    from app.services.attachments import extract_body

    text, note = extract_body(body.name, body.media_type, body.data)
    if note:
        raise HTTPException(status_code=400, detail=note[0].upper() + note[1:])

    cap = settings.projects_file_max_chars
    truncated = len(text) > cap
    if truncated:
        text = text[:cap] + f"\n\n[… truncated at {cap} characters]"

    record = ProjectFile(
        project_id=project.id,
        name=body.name[:255],
        media_type=(body.media_type or "")[:128],
        size_bytes=body.size or 0,
        chars=len(text),
        content=text,
    )
    db.add(record)
    project.last_activity_at = _now()
    project.updated_at = _now()
    await db.commit()
    await db.refresh(record)
    # `file_name`, not `name`: `name` is a reserved LogRecord attribute, and
    # passing it via extra= raises inside logging itself — which surfaces as a
    # 500 on an upload that had, by then, already been committed.
    logger.info(
        "project_file_added",
        extra={"project_id": project.id, "file_name": record.name, "chars": record.chars},
    )
    return {
        "id": record.id,
        "name": record.name,
        "media_type": record.media_type,
        "size": record.size_bytes,
        "chars": record.chars,
        "created_at": record.created_at.isoformat(),
        "truncated": truncated,
    }


@router.delete("/{project_id}/files/{file_id}")
async def delete_project_file(
    project_id: int,
    file_id: int,
    agent_id: str = "speda",
    db: AsyncSession = Depends(get_db),
):
    project = await _owned(db, project_id, agent_id)
    await db.execute(
        delete(ProjectFile).where(
            ProjectFile.id == file_id, ProjectFile.project_id == project.id
        )
    )
    project.updated_at = _now()
    await db.commit()
    return {"ok": True}
