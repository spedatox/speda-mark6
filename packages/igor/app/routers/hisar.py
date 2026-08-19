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

# The same client the agents use, so the owner's picker shows exactly what
# their agents see and a directory chosen here is valid input for the tool they
# call. `entries()` needs no AgentContext — it is a plain read.
HISAR = HisarSkill()


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
