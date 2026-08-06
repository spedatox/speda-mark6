"""Hisar directory browser — lets the Heartbreaker UI browse the owner's vault
so they can pick a workspace directory for Optimus/Forge jobs.

This is NOT a user-facing chat endpoint — it is the UI's file-picker
replacement. It uses the same Hisar client (HisarSkill) the agents use, so the
owner sees exactly what their agents see, and a directory they pick here is
valid input for the hisar tool the agents call.
"""

import logging

from fastapi import APIRouter, HTTPException, Request

from app.config import settings
from app.skills.hisar import HisarSkill

logger = logging.getLogger(__name__)
router = APIRouter(tags=["hisar"])

# Reuse the same skill instance the orchestrator uses, but stateless: list/read
# actions need no AgentContext beyond a request_id for logging.
HISAR = HisarSkill()


class _Ctx:
    """Minimal context stub — HisarSkill.execute() only reads request_id."""
    def __init__(self, request_id: str):
        self.request_id = request_id


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

    ctx = _Ctx(request_id=str(id(request)))
    try:
        raw = await HISAR._list(path.strip() or "/")
    except Exception as e:
        logger.warning(
            "hisar_dirs_failed",
            extra={"path": path, "error": str(e)},
        )
        raise HTTPException(
            status_code=502,
            detail=f"Hisar did not answer for {path!r}. The vault may be unreachable.",
        )

    # Parse the skill's listing output ("/path\n  name/  \n  other/") → ["name", "other"]
    dirs: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.endswith("/") and not stripped.startswith("…"):  # skip "… and N more"
            dirs.append(stripped.rstrip("/"))

    # Sort: leading underscore/prefix (SPEDA/, Forge/) first, then alpha.
    dirs.sort(key=lambda d: (not d.startswith(("SPEDA", "Forge", "Projects", "Documents")), d.lower()))

    return {"path": path.strip() or "/", "dirs": dirs}
