# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The Skyfall Protocol's HTTP surface: manage projects, arm one, fire or abort.

Thin per Rule 1 — validation, masking, resolution and the request itself all
live in services/skyfall.py.

X-API-Key only (AuthMiddleware, Rule 12). Deliberately no n8n secret and no
`/trigger` path anywhere near this: nothing automated may reach these. The fire
endpoint exists to be called by a client whose countdown just ran out, with the
owner watching it, and pointing a cron at it would be firing the protocol with
the screen — the only part that can say no — removed.

The arm endpoint returns the countdown payload rather than starting anything.
Starting is the client drawing the clock.
"""

import logging

from fastapi import APIRouter, HTTPException

from app.schemas.skyfall import (
    SkyfallAbortRequest,
    SkyfallArm,
    SkyfallFireRequest,
    SkyfallProject,
    SkyfallProjectWrite,
    SkyfallResult,
)
from app.services import skyfall

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/protocols/skyfall", tags=["skyfall"])


@router.get("/projects", response_model=list[SkyfallProject])
async def list_projects():
    """Every configured launch target, header values masked."""
    return skyfall.listing()


@router.put("/projects", response_model=SkyfallProject)
async def upsert_project(body: SkyfallProjectWrite):
    """Create or update one project. Empty `id` creates.

    The owner is the only author — there is no tool that reaches this, by
    design. A 400 carries the reason in words the pane can show as-is."""
    saved, problem = skyfall.save(body.model_dump())
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    return saved


@router.delete("/projects/{project_id}")
async def delete_project(project_id: str):
    """Forget a project. Its countdown history goes with it."""
    return {"deleted": skyfall.remove(project_id)}


@router.post("/arm/{project_id}", response_model=SkyfallArm)
async def arm(project_id: str):
    """What the client needs to draw the countdown for this project.

    Changes nothing and sends nothing — arming IS opening the clock, and the
    clock is the client's to run."""
    project = skyfall.get_skyfall_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="No such Skyfall project.")
    return skyfall.arming_payload(project)


@router.post("/fire", response_model=SkyfallResult)
async def fire(body: SkyfallFireRequest):
    """Send the project's request. Called when a countdown reached zero.

    Returns 200 even when the endpoint answered badly: `fired` and `ok` are
    separate fields precisely so the screen can tell "it went out and came back
    500" apart from "it never left", and an HTTP error status here would
    collapse the two."""
    ok, result = await skyfall.fire(body.project_id)
    if not result.get("fired"):
        # Nothing was sent — a deleted or unusable project. That IS an error.
        raise HTTPException(status_code=409, detail=result.get("error", "not fired"))
    logger.info("skyfall_fire_result", extra={"project": body.project_id, "ok": ok})
    return result


@router.post("/abort")
async def abort(body: SkyfallAbortRequest):
    """The owner stopped the clock. Nothing was sent; this only records it."""
    return skyfall.abort(body.project_id, body.remaining_seconds)
