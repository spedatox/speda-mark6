# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Liveness probe + the Atomix health-sync ingestion surface.

Note the auth asymmetry, and keep it: GET /health is the ONLY unauthenticated
path here (AuthMiddleware matches it exactly, not as a prefix), so every
/health/* endpoint below still requires X-API-Key per Rule 12. Adding a route
under this prefix does not inherit the probe's exemption — asserted by
tests/test_health_sync.py so a future refactor to prefix matching can't quietly
expose the owner's biometrics.

Thin per Rule 1: all ingest/rollup/query logic lives in services/health.py.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import runtime_state
from app.database import get_db
from app.schemas.health import HealthIngestRequest
from app.services import health as health_service

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    registry = getattr(request.app.state, "registry", None)
    tools = registry.list_tools() if registry else []
    return JSONResponse(
        {
            "status": "ok",
            "tools_registered": len(tools),
        }
    )


@router.post("/health/ingest")
async def health_ingest(body: HealthIngestRequest, db: AsyncSession = Depends(get_db)):
    """Accept a batch of biometrics from Speda GO. Idempotent on
    (metric, start_ts, origin), so the phone can safely re-send a batch whose
    POST failed — duplicates are counted, not stored twice."""
    return await health_service.ingest_samples(
        db, [s.model_dump() for s in body.samples], device=body.device
    )


@router.get("/health/status")
async def health_status(db: AsyncSession = Depends(get_db)):
    """Sync state for the phone's Settings ▸ HEALTH tab: sample counts per
    metric, last ingest, and the covered day span."""
    return await health_service.status(db)


@router.get("/health/freshness")
async def health_freshness(db: AsyncSession = Depends(get_db)):
    """Per-metric age of the newest sample against its staleness budget — what
    tells a briefing whether it may speak in the present tense. Also surfaces
    any outstanding sync demand so the phone's HEALTH tab can show that Igor
    asked for data and did not get it."""
    report = await health_service.freshness(db)
    return {
        "metrics": report,
        "stale": health_service.stale_metrics(report),
        "demand": runtime_state.get_health_sync_demand(),
    }


@router.get("/health/sync-demand")
async def health_sync_demand():
    """Polled by Speda GO: is Igor waiting on a sync right now?

    Speda GO carries no Firebase, so nothing can wake it from the server side —
    this is a note left where the app will look (on foreground, and after each
    scheduled sync). `outstanding` is true only while a demand is unserved; the
    app should sync immediately when it sees it, which clears it via ingest.
    """
    demand = runtime_state.get_health_sync_demand()
    return {
        "outstanding": bool(demand and not demand.get("served_at")),
        "at": demand.get("at", 0),
        "reason": demand.get("reason", ""),
    }


@router.delete("/health/data")
async def health_wipe(db: AsyncSession = Depends(get_db)):
    """DISCONNECT + WIPE. Deletes every stored sample and rollup."""
    return await health_service.wipe(db)
