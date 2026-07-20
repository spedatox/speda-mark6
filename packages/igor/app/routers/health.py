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
    """Accept a batch of biometrics from Heartbreaker Core. Idempotent on
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


@router.delete("/health/data")
async def health_wipe(db: AsyncSession = Depends(get_db)):
    """DISCONNECT + WIPE. Deletes every stored sample and rollup."""
    return await health_service.wipe(db)
