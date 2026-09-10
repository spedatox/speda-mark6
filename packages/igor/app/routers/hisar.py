# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Hisar directory browser — lets the Heartbreaker UI browse the owner's vault
so they can pick a workspace directory for Optimus/Forge jobs.

This is NOT a user-facing chat endpoint — it is the UI's file-picker
replacement. It uses the same Hisar client (HisarSkill) the agents use, so the
owner sees exactly what their agents see, and a directory they pick here is
valid input for the hisar tool the agents call.
"""

import logging
import os
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.config import settings
from app.skills.hisar import HisarSkill

logger = logging.getLogger(__name__)
router = APIRouter(tags=["hisar"])

# The same client the agents use, so the owner's picker shows exactly what
# their agents see and a directory chosen here is valid input for the tool they
# call. `entries()` needs no AgentContext — it is a plain read.
HISAR = HisarSkill()


class CreateDirectoryRequest(BaseModel):
    path: str
    name: str


def _forge_workspace_parent(vault_path: str) -> tuple[Path, Path]:
    """Translate a picker path while confining writes to Forge workspaces."""
    if not settings.forge_workspace_root:
        raise HTTPException(status_code=503, detail="Forge workspace creation is not configured.")

    wire = PurePosixPath(vault_path.strip() or "/")
    prefix = ("/", "Forge", "workspaces")
    if wire.parts[:3] != prefix or ".." in wire.parts:
        raise HTTPException(
            status_code=403,
            detail="New folders can be created only under /Forge/workspaces.",
        )

    root = Path(settings.forge_workspace_root).expanduser().resolve()
    parent = root.joinpath(*wire.parts[3:]).resolve()
    try:
        parent.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Workspace path escapes the allowed root.") from exc
    if not parent.is_dir():
        raise HTTPException(status_code=404, detail="The parent workspace folder does not exist.")
    return root, parent


@router.get("/hisar/dirs")
async def hisar_dirs(request: Request, path: str = "/"):
    """List directories in the Hisar vault at `path`.

    Returns a flat list of dirs only — files and symlinks are excluded, because
    the UI is a directory picker, not a full file browser. Empty result means
    the folder has no subdirectories (not that the vault is unreachable — that
    surfaces as a 502).

    The endpoint is authenticated (AuthMiddleware) and requires the Hisar
    machine token to be configured — without it, Hisar itself is unreachable and
    the picker has nothing to show.
    """
    if not settings.hisar_machine_token:
        raise HTTPException(
            status_code=503,
            detail="Hisar is not configured on this deployment (no machine token).",
        )

    target = path.strip() or "/"
    try:
        entries = await HISAR.entries(target)
    except Exception as e:
        logger.warning(
            "hisar_dirs_failed",
            extra={"path": target, "error": str(e)},
        )
        raise HTTPException(
            status_code=502,
            detail=f"Hisar did not answer for {target!r}. The vault may be unreachable.",
        )

    # Straight from the data. Recovering this by parsing the skill's RENDERED
    # listing put an entry named "" at the top of every root listing, because
    # the header line `/` also ends in a slash — a blank row in the picker,
    # on the first screen it shows.
    dirs = [e["name"] for e in entries if HISAR.is_dir(e) and e.get("name")]

    # Sort: leading underscore/prefix (Speda/, Forge/) first, then alpha.
    dirs.sort(key=lambda d: (not d.startswith(("Speda", "Forge", "Projects", "Documents")), d.lower()))

    return {"path": path.strip() or "/", "dirs": dirs}


@router.post("/hisar/dirs", status_code=201)
async def create_hisar_dir(request: Request, body: CreateDirectoryRequest):
    """Create and return a new Forge workspace directory for the picker."""
    del request  # Authentication is enforced by AuthMiddleware before routing.
    name = body.name.strip()
    if (
        not name
        or len(name) > 100
        or name in {".", ".."}
        or name.startswith(".")
        or "/" in name
        or "\\" in name
        or any(ord(char) < 32 for char in name)
    ):
        raise HTTPException(
            status_code=422,
            detail="Use a visible folder name of 1–100 characters without slashes.",
        )

    root, parent = _forge_workspace_parent(body.path)
    destination = (parent / name).resolve()
    try:
        destination.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Workspace path escapes the allowed root.") from exc
    if destination.exists():
        raise HTTPException(status_code=409, detail=f"A folder named {name!r} already exists.")

    try:
        destination.mkdir(mode=0o2775)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=f"A folder named {name!r} already exists.") from exc

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
        raise HTTPException(status_code=500, detail="Could not create the workspace folder.") from exc

    created = str(PurePosixPath(body.path.rstrip("/") or "/") / name)
    return {"path": created, "name": name}
