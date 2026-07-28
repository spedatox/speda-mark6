"""
Route geometry lookup for the map card.

Thin per Rule 1 — the store lives in services/routes.py. Authenticated by
`X-API-Key` via AuthMiddleware like everything else (Rule 12); both clients
already send it on every call.

This endpoint is what lets the ```map fence carry a routeId instead of a
polyline. See models/route.py for why that matters.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services import routes as route_store

logger = logging.getLogger(__name__)
router = APIRouter(tags=["navigation"])


@router.get("/navigation/route/{route_id}")
async def get_route_geometry(route_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """The real geometry for a route the agent referenced by id.

    404 when the id is unknown — an expired or mistyped id must fail visibly.
    The whole point of this endpoint is that a wrong reference cannot quietly
    render as a plausible wrong line.
    """
    route = await route_store.fetch(db, route_id.strip())
    if route is None:
        logger.info("route_geometry_miss", extra={"route_id": route_id[:24]})
        raise HTTPException(status_code=404, detail=f"No route geometry for '{route_id}'")
    return route
