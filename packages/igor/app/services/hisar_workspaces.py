"""Hisar's directory picker and confined workspace creation.

The app owns this service and the shared Hisar client. Routers translate its
typed failures, without filesystem operations or a module-level client.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path, PurePosixPath

from app.services.workspaces import WorkspaceError

logger = logging.getLogger(__name__)


class HisarWorkspaceService:
    def __init__(self, root: str, client, *, configured: bool):
        self.root = root
        self.client = client
        self.configured = configured

    async def directories(self, path: str):
        if not self.configured:
            raise WorkspaceError("Hisar is not configured on this deployment (no machine token).", 503)
        target = path.strip() or "/"
        try:
            entries = await self.client.entries(target)
        except Exception as exc:
            logger.warning("hisar_dirs_failed", extra={"path": target, "error": str(exc)})
            raise WorkspaceError(f"Hisar did not answer for {target!r}. The vault may be unreachable.", 502) from exc
        dirs = [entry["name"] for entry in entries if self.client.is_dir(entry) and entry.get("name")]
        dirs.sort(key=lambda name: (not name.startswith(("Speda", "Forge", "Projects", "Documents")), name.lower()))
        return {"path": target, "dirs": dirs}

    def _parent(self, vault_path: str):
        if not self.root:
            raise WorkspaceError("Forge workspace creation is not configured.", 503)
        wire = PurePosixPath(vault_path.strip() or "/")
        if wire.parts[:3] != ("/", "Forge", "workspaces") or ".." in wire.parts:
            raise WorkspaceError("New folders can be created only under /Forge/workspaces.", 403)
        root = Path(self.root).expanduser().resolve()
        parent = root.joinpath(*wire.parts[3:]).resolve()
        try:
            parent.relative_to(root)
        except ValueError as exc:
            raise WorkspaceError("Workspace path escapes the allowed root.", 403) from exc
        if not parent.is_dir():
            raise WorkspaceError("The parent workspace folder does not exist.", 404)
        if parent.is_relative_to(root / ".forge"):
            raise WorkspaceError("Coordinator metadata is not a workspace creation area.", 403)
        return root, parent

    async def create(self, path: str, name: str):
        return await asyncio.to_thread(self._create, path, name)

    def _create(self, path: str, name: str):
        name = name.strip()
        if (not name or len(name) > 100 or name in {".", ".."} or name.startswith(".")
                or "/" in name or "\\" in name or any(ord(char) < 32 for char in name)):
            raise WorkspaceError("Use a visible folder name of 1–100 characters without slashes.", 422)
        root, parent = self._parent(path)
        destination = (parent / name).resolve()
        try:
            destination.relative_to(root)
        except ValueError as exc:
            raise WorkspaceError("Workspace path escapes the allowed root.", 403) from exc
        if destination.exists():
            raise WorkspaceError(f"A folder named {name!r} already exists.", 409)
        try:
            destination.mkdir(mode=0o2775)
        except FileExistsError as exc:
            raise WorkspaceError(f"A folder named {name!r} already exists.", 409) from exc
        except OSError as exc:
            raise WorkspaceError("Could not create the workspace folder.", 500) from exc
        try:
            if os.name != "nt":
                os.chown(destination, -1, root.stat().st_gid)
            destination.chmod(0o2775)
        except OSError as exc:
            try:
                destination.rmdir()
            except OSError:
                pass
            logger.exception("hisar_workspace_create_failed", extra={"path": str(destination)})
            raise WorkspaceError("Could not create the workspace folder.", 500) from exc
        created = str(PurePosixPath(path.strip().rstrip("/") or "/") / name)
        return {"path": created, "name": name}
