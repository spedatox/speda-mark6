"""
Place-set store — the source of truth for what `find_places` actually found.

See models/place.py for why this exists. The rule mirrors the route store: the
directory goes from Google to the database to the client, and the model only
ever holds a reference plus a summary it can reason over.

The record shape stored here IS the client contract (GET /navigation/places/{id}):

    {
      "placeId": "ChIJ…",          # Google's own id — stable, links back
      "name": "Kuaför Emre",
      "lat": 40.21, "lng": 28.99,
      "address": "Nilüfer, Bursa",
      "category": "Barber shop",   # primaryTypeDisplayName
      "rating": 4.6, "reviews": 231,
      "openNow": true,
      "priceLevel": "moderate",
      "phone": "+90 …", "website": "https://…",
      "mapsUri": "https://maps.google.com/…",
      "hours": ["Monday: 09:00–20:00", …],
      "summary": "Traditional barbershop …",
      "distanceKm": 0.8            # from the search centre, when there is one
    }

Every field is optional except name/lat/lng — Places returns a ragged set and a
card that hides what is missing reads better than one full of "—".
"""

import json
import logging
import math
import secrets
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.place import RETENTION_DAYS, PlaceSet

logger = logging.getLogger(__name__)


def new_place_set_id() -> str:
    """Ten characters, same discipline as a routeId: reproduced exactly or
    obviously wrong, with no quiet middle ground."""
    return "pl_" + secrets.token_hex(4)


def haversine_km(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    """Great-circle distance in km. Straight-line, not driving distance — it is
    used to sort and label 'how far is this', where the road-network answer would
    cost one Routes call per result."""
    r = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = math.radians(b_lat - a_lat), math.radians(b_lng - a_lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


async def store(
    db: AsyncSession,
    query: str,
    centre: dict | None,
    places: list[dict],
) -> str | None:
    """Persist this turn's place results; return the set's id.

    Best-effort: None means nothing was stored, and the caller falls back to
    telling the model to write plain markers rather than losing the answer.
    """
    if db is None:
        logger.warning("places_store_no_session")
        return None
    try:
        row = PlaceSet(
            id=new_place_set_id(),
            query=(query or "")[:200],
            center_lat=centre["lat"] if centre else None,
            center_lng=centre["lng"] if centre else None,
            places_json=json.dumps(places, ensure_ascii=False),
        )
        db.add(row)
        # Pruned here rather than on a schedule: one cheap DELETE on an indexed
        # column, run exactly when the table grows.
        await db.execute(
            delete(PlaceSet).where(
                PlaceSet.created_at < datetime.utcnow() - timedelta(days=RETENTION_DAYS)
            )
        )
        await db.commit()
        logger.info("places_stored", extra={"count": len(places)})
        return row.id
    except Exception as exc:  # noqa: BLE001
        logger.error("places_store_failed", extra={"error": str(exc)})
        await db.rollback()
        return None


async def fetch(db: AsyncSession, set_id: str) -> dict | None:
    """One stored result set, shaped for the client's map card."""
    row = (await db.execute(
        select(PlaceSet).where(PlaceSet.id == set_id)
    )).scalars().first()
    if row is None:
        return None
    try:
        places = json.loads(row.places_json or "[]")
    except (ValueError, TypeError):
        places = []
    return {
        "placesId": row.id,
        "query": row.query or None,
        "center": ({"lat": row.center_lat, "lng": row.center_lng}
                   if row.center_lat is not None else None),
        "places": places,
    }
