"""Owner-controlled relocation and integration with retained rollback artifacts."""
import asyncio
import os
from pathlib import Path
import shutil
from uuid import uuid4
from app.database import write_session
from sqlalchemy import select, update
from app.models.coordination import Project, ProjectTask, ProjectOperation
from app.services.coordination_budget import CoordinationError
from app.services.projects import inventory, snapshot


def apply_snapshot(source, target, expected_source, expected_target):
    actual_source, source_rows = inventory(source)
    actual_target, target_rows = inventory(target)
    if (actual_source, actual_target) != (expected_source, expected_target):
        raise CoordinationError("revision_changed", "Files changed since the integration was prepared")
    old = {name: (sha, mode) for name, sha, mode in target_rows}
    new = {name: (sha, mode) for name, sha, mode in source_rows}
    # Refuse file/directory shape conflicts; never recursively remove a tree.
    for name in new:
        destination = target / name
        if destination.is_dir() or any(p.is_file() for p in destination.parents if p != target):
            raise CoordinationError("shape_conflict", "Resolve file/directory conflicts before integration")
    for name in sorted(set(old) - set(new)):
        (target / name).unlink()
    for name, signature in new.items():
        if old.get(name) == signature:
            continue
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".integration-" + uuid4().hex)
        shutil.copy2(source / name, temporary)
        os.replace(temporary, destination)
    if inventory(target)[0] != expected_source:
        raise CoordinationError("integration_changed", "Integrated files require reconciliation")


class ProjectLifecycle:
    def __init__(self, projects):
        self.projects, self.sessions = projects, projects.sessions

    async def relocate(self, owner, project_id, body):
        target = self.projects.path(body.path)
        if (await asyncio.to_thread(inventory, target))[0] != body.expected_content_revision:
            raise CoordinationError("revision_changed", "Relocation target differs from the expected content")
        async with write_session(self.sessions) as db:
            await db.execute(update(Project).where(Project.id == project_id, Project.user_id == owner)
                .values(location_version=Project.location_version))
            project = await db.get(Project, project_id)
            if not project or project.user_id != owner:
                raise CoordinationError("not_found", "Project unavailable", 404)
            if project.location_version != body.expected_location_version or project.host_id != self.projects.settings.project_host_id:
                raise CoordinationError("location_conflict", "Project location or host changed")
            active = await db.scalar(select(ProjectTask.id).where(ProjectTask.project_id == project_id,
                ProjectTask.status.in_(["queued", "staging", "running", "reconciling"])).limit(1))
            if active:
                raise CoordinationError("project_active", "Finish or reconcile outstanding project tasks before relocation")
            old = project.path
            await asyncio.to_thread(self.projects.workshop.relocate, project.legacy_id, target)
            project.path, project.location_version = str(target), project.location_version + 1
            db.add(ProjectOperation(id=uuid4().hex, project_id=project_id, kind="relocate", status="complete",
                details={"from": old, "to": str(target), "revision": body.expected_content_revision}))
        return await self.projects.inspect(owner, project_id)

    async def integrate(self, owner, project_id, body):
        project = await self.projects.owned(owner, project_id)
        async with self.sessions() as db:
            task = await db.get(ProjectTask, body.task_id)
            if not task or task.project_id != project_id or task.status != "succeeded" or not task.result:
                raise CoordinationError("artifact_unavailable", "Only a completed artifact from this project can be integrated")
            if task.result.get('cloud_environment'):
                raise CoordinationError('cloud_pr_required', 'Open a pull request for changes from a cloud environment', 409)
        artifact = Path(task.result["workspace"])
        expected_path = self.projects.state / "artifacts" / task.id
        if artifact.resolve() != expected_path.resolve() or task.result.get("revision") != body.expected_artifact_revision:
            raise CoordinationError("artifact_changed", "Artifact identity or revision differs")
        return await self._apply(project, artifact, body.expected_artifact_revision,
            body.expected_source_revision, "integrate", {"task_id": task.id})

    async def rollback(self, owner, operation_id, expected_revision):
        async with self.sessions() as db:
            operation = await db.get(ProjectOperation, operation_id)
        if not operation or operation.kind not in {"integrate", "rollback"} or not operation.details.get("backup"):
            raise CoordinationError("rollback_unavailable", "No retained rollback artifact", 404)
        project = await self.projects.owned(owner, operation.project_id)
        if operation.status != "complete":
            raise CoordinationError("reconciliation_required", "Interrupted integration needs operator reconciliation before another writer")
        return await self._apply(project, Path(operation.details["backup"]), operation.details["before_revision"],
            expected_revision, "rollback", {"reverts": operation_id})

    async def _apply(self, project, artifact, expected_artifact, expected_source, kind, extra):
        source = Path(project.path)
        if (await asyncio.to_thread(inventory, source))[0] != expected_source or (
                await asyncio.to_thread(inventory, artifact))[0] != expected_artifact:
            raise CoordinationError("revision_changed", "Source or artifact changed; inspect the new revision")
        identity = uuid4().hex
        claim = "integration-" + identity
        await asyncio.to_thread(self.projects.workshop.claim, source, claim, f"Owner {kind} {identity}")
        backup = self.projects.state / "rollback" / identity
        details = {**extra, "claim": claim, "backup": str(backup), "before_revision": expected_source,
            "after_revision": expected_artifact, "artifact": str(artifact)}
        try:
            await asyncio.to_thread(snapshot, source, backup)
            async with write_session(self.sessions) as db:
                db.add(ProjectOperation(id=identity, project_id=project.id, kind=kind, details=details))
            await asyncio.to_thread(apply_snapshot, artifact, source, expected_artifact, expected_source)
            await asyncio.to_thread(self.projects.workshop.finish, claim, "succeeded", f"Revision {expected_artifact}")
            async with write_session(self.sessions) as db:
                await db.execute(update(ProjectOperation).where(ProjectOperation.id == identity).values(status="complete"))
        except BaseException:
            await asyncio.shield(asyncio.to_thread(self.projects.workshop.interrupt, claim, "Integration interrupted; inspect retained backup"))
            async with write_session(self.sessions) as db:
                await db.execute(update(ProjectOperation).where(ProjectOperation.id == identity).values(status="reconciling"))
            raise
        return {"id": identity, "status": "complete", **details}
