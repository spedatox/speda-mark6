# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Bind private conversations to shared engineering desks, once.

Reference: Codex state/src/model/thread_metadata.rs persists a thread's cwd.
Here the persistent workshop ID owns the canonical path; Igor stores only the
reference. Historic unbound chats never adopt today's global picker selection.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
import sqlite3

from sqlalchemy import exists, select, update

from app.core.context import AgentContext
from app.models.message import Message
from app.models.project import Project
from app.models.session import Session


class WorkspaceError(ValueError):
    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class WorkspaceBinding:
    project_id: str
    path: str


class WorkspaceService:
    def __init__(self, root: str):
        self.root = root

    def _project(self, project_id: str | None, workspace: str | None) -> WorkspaceBinding:
        from app.execution.forge import _resolve_workspace
        from forge.workshop import Workshop

        if not self.root:
            raise WorkspaceError("A persistent Forge workspace root is required.", 503)
        try:
            store = Workshop(Path(self.root), readonly=bool(project_id))
            if project_id:
                record = store.inspect(project_id)
                path = _resolve_workspace(record["path"], allowed_root=self.root)
                if not path.is_dir() or not record["available"]:
                    raise WorkspaceError("The saved desk is unavailable. Restore it before continuing.", 409)
            else:
                if not workspace:
                    raise WorkspaceError("Select a registered engineering desk or existing workspace.", 422)
                path = _resolve_workspace(workspace, allowed_root=self.root)
                record = store.register(path)
            coordinator = Path(self.root).expanduser().resolve() / ".forge"
            if path.is_relative_to(coordinator):
                raise WorkspaceError("Coordinator metadata cannot be an engineering desk.", 422)
            return WorkspaceBinding(record["id"], str(path))
        except WorkspaceError:
            raise
        except (ValueError, OSError, sqlite3.Error) as exc:
            raise WorkspaceError(f"Engineering desk is unavailable: {exc}", 422) from exc

    async def _owned(self, context: AgentContext):
        session = (await context.db.execute(select(Session).where(
            Session.id == context.session_id, Session.user_id == context.user_id,
            Session.agent_id == context.agent_id,
        ).execution_options(populate_existing=True))).scalar_one_or_none()
        if session is None:
            raise WorkspaceError("No conversation for this owner and agent.", 404)
        project = None
        if session.project_id is not None:
            project = (await context.db.execute(select(Project).where(
                Project.id == session.project_id, Project.user_id == context.user_id,
                Project.agent_id == context.agent_id,
            ).execution_options(populate_existing=True))).scalar_one_or_none()
            if project is None:
                raise WorkspaceError("No private project for this owner and agent.", 404)
        return session, project

    async def _save(self, context: AgentContext, session, project, chosen: WorkspaceBinding):
        # Conditional updates prevent simultaneous first selections from winning
        # with different desks. Both Igor references commit in one transaction.
        try:
            for model, row in ((Session, session), (Project, project)):
                if row is None:
                    continue
                await context.db.execute(update(model).where(
                    model.id == row.id, model.user_id == context.user_id,
                    model.agent_id == context.agent_id, model.workshop_project_id.is_(None),
                ).values(workshop_project_id=chosen.project_id))
            latest_session, latest_project = await self._owned(context)
            if any(row is not None and row.workshop_project_id != chosen.project_id
                   for row in (latest_session, latest_project)):
                raise WorkspaceError("This conversation or private project is bound to another desk. Start a new chat/project.")
            await context.db.commit()
        except BaseException:
            await context.db.rollback()
            raise

    @staticmethod
    def _apply(context: AgentContext, chosen: WorkspaceBinding):
        context.workshop_project_id = chosen.project_id
        context.extra["cwd"] = chosen.path
        context.extra.pop("workspace_binding_required", None)
        return chosen

    async def prepare(
        self, context: AgentContext, *, workspace: str | None = None,
        project_id: str | None = None, explicit: bool = False,
    ) -> WorkspaceBinding | None:
        """Restore a binding, or snapshot a new chat's initial picker selection.

        Explicit workshop selection may bind a historic unbound chat. It cannot
        repoint a bound chat. A request's global cwd is ignored for bound chats.
        """
        session, project = await self._owned(context)
        bound = session.workshop_project_id
        inherited = project.workshop_project_id if project else None
        if bound:
            if inherited and inherited != bound:
                raise WorkspaceError("Conversation and private project reference different desks. Reconcile their provenance before continuing.")
            chosen = await asyncio.to_thread(self._project, bound, None)
            if explicit or project_id:
                requested = await asyncio.to_thread(self._project, project_id, workspace) if (project_id or workspace) else chosen
                if requested.project_id != bound:
                    raise WorkspaceError("This chat is bound to another desk. Start a new chat for that desk.")
            if project is not None and inherited is None:
                await self._save(context, session, project, chosen)
            return self._apply(context, chosen)

        if not self.root:
            if project_id or inherited:
                raise WorkspaceError("Persistent desk resolution is not configured.", 503)
            if workspace:
                context.extra["cwd"] = workspace  # existing unrestricted local development
            return None
        historical = bool(await context.db.scalar(select(exists().where(Message.session_id == session.id))))
        if historical and not explicit:
            # Do not invent provenance from a preference or a later project link.
            context.extra.pop("cwd", None)
            context.extra["workspace_binding_required"] = True
            return None
        if not (project_id or inherited or workspace):
            return None
        chosen = await asyncio.to_thread(self._project, project_id or inherited, workspace)
        if inherited and inherited != chosen.project_id:
            raise WorkspaceError("The private project is already linked to another desk.")
        await self._save(context, session, project, chosen)
        return self._apply(context, chosen)
