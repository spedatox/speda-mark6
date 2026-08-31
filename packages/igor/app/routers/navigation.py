# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Map-card lookups: route geometry and place-search results.

Thin per Rule 1 — the stores live in services/routes.py and services/places.py.
Authenticated by `X-API-Key` via AuthMiddleware like everything else (Rule 12);
both clients already send it on every call.

These endpoints are what let a ```map fence carry a routeId instead of a
polyline, and a placesId instead of a transcribed directory. See models/route.py
and models/place.py for why that matters.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services import places as place_store
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


@router.get("/navigation/places/{set_id}")
async def get_place_set(set_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """The full result set for a place search the agent referenced by id.

    404 on an unknown id, for the same reason as routes: a card that silently
    renders nothing is indistinguishable from a search that found nothing, and
    those are different answers.
    """
    result = await place_store.fetch(db, set_id.strip())
    if result is None:
        logger.info("place_set_miss", extra={"set_id": set_id[:24]})
        raise HTTPException(status_code=404, detail=f"No place set for '{set_id}'")
    return result
