"""
Live aircraft position: stateless passthrough to adsb.lol, keyed by tail
number.

Thin per Rule 1, mirrors routers/navigation.py — except there is no id-store
behind it. A route/place lookup parks an immutable snapshot behind a
generated id specifically to protect a long polyline from being retyped; a
tail number is short and carries no such risk, and the position it points to
changes every few seconds anyway. So this endpoint is what both clients poll
directly by tail number to move a marker, with the upstream call itself
staying server-side (services/aircraft.py) rather than exposing that API to
the client. Authenticated by `X-API-Key` via AuthMiddleware like every route.
"""

import logging

from fastapi import APIRouter, HTTPException

from app.services import aircraft as aircraft_service

logger = logging.getLogger(__name__)
router = APIRouter(tags=["aircraft"])


@router.get("/aircraft/track/{tail_number}")
async def get_aircraft_track(tail_number: str) -> dict:
    """Current live position + ADS-B status for a tail number.

    404 when there is no current signal — a plane that has landed or gone out
    of coverage since the fence was rendered must fail visibly, not freeze the
    marker on a stale-but-plausible position.
    """
    result = await aircraft_service.track(tail_number.strip())
    if result is None:
        logger.info("aircraft_track_miss", extra={"tail_number": tail_number[:16]})
        raise HTTPException(status_code=404, detail=f"No live position for '{tail_number}'")
    return result
