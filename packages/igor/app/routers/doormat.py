# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The owner-facing Doormat Protocol: move the deployment to a new domain.

Thin per Rule 1 — every phase, precondition and rollback lives in
services/doormat.py. X-API-Key only (AuthMiddleware, Rule 12): these are for the
owner and for Orion acting on their word, never for a poller, so there is no
n8n secret on them and no automation should ever be pointed at them.

Each phase runs synchronously and returns what actually happened to the host
alongside the resulting state, rather than a flipped flag — the same posture as
/agents/lockdown, and for the same reason: a client must not be able to render
"moved" over a refusal.
"""

import logging

from fastapi import APIRouter, HTTPException

from app.schemas.doormat import (
    DoormatActionResponse,
    DoormatStageRequest,
    DoormatState,
)
from app.services import doormat

logger = logging.getLogger(__name__)
router = APIRouter(tags=["doormat"])


@router.get("/host/doormat", response_model=DoormatState)
async def doormat_state():
    """Where a domain change has got to, plus what Caddy is really serving and
    whether the third-party console steps are still outstanding."""
    return await doormat.status()


@router.post("/host/doormat/stage", response_model=DoormatActionResponse)
async def stage(body: DoormatStageRequest):
    """Serve a new domain alongside the current one, with a real certificate.

    Refuses unless the domain already resolves to this server — a Caddy site for
    a hostname that points elsewhere spends the Let's Encrypt rate limit and
    never gets a certificate. Rolls itself back on any failure."""
    ok, report = await doormat.stage(body.domain, force=body.force)
    if not ok:
        raise HTTPException(status_code=409, detail=report)
    return DoormatActionResponse(ok=ok, report=report, state=await doormat.status())


@router.post("/host/doormat/cutover", response_model=DoormatActionResponse)
async def cutover():
    """Repoint Igor's own settings at the staged domain.

    Both hostnames keep serving. Only safe once the third-party redirect URIs
    have been updated — the state's `checklist` is what says which. Igor must be
    restarted afterwards for the new values to take effect."""
    ok, report = await doormat.cutover()
    if not ok:
        raise HTTPException(status_code=409, detail=report)
    return DoormatActionResponse(ok=ok, report=report, state=await doormat.status())


@router.post("/host/doormat/retire", response_model=DoormatActionResponse)
async def retire():
    """Stop serving the old domain and make the deployment file match.

    The last step and the only one that recreates a container, so Caddy is down
    for a few seconds. Refuses while the new domain is not serving, or while the
    cutover settings have been written but not yet loaded by a restart."""
    ok, report = await doormat.retire()
    if not ok:
        raise HTTPException(status_code=409, detail=report)
    return DoormatActionResponse(ok=ok, report=report, state=await doormat.status())


@router.delete("/host/doormat", response_model=DoormatActionResponse)
async def abort():
    """Undo a staged domain and forget the protocol state. Refuses after cutover
    — by then the settings have moved, and dropping the site would leave them
    naming a hostname nothing serves."""
    ok, report = await doormat.abort()
    if not ok:
        raise HTTPException(status_code=409, detail=report)
    return DoormatActionResponse(ok=ok, report=report, state=await doormat.status())
