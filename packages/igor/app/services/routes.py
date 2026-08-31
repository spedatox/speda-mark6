# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Route geometry store — the source of truth for what a route actually looks like.

See models/route.py for why this exists at all. The rule it enforces is simple:
the polyline goes from Google to the database to the client, and the model only
ever holds a reference to it.
"""

import json
import logging
import secrets
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.route import RETENTION_DAYS, RouteGeometry

logger = logging.getLogger(__name__)


def new_route_id() -> str:
    """Ten characters, unambiguous when read back. Short enough that a model
    either reproduces it exactly or produces something that plainly is not an
    id — no quiet middle ground."""
    return "r_" + secrets.token_hex(4)


async def store(db: AsyncSession, routes: list[dict]) -> list[str]:
    """Persist this turn's routes; return their ids in the same order.

    Best-effort: if the store fails the caller still has the polylines and can
    fall back to the legacy inline path rather than losing the answer entirely.
    """
    if db is None:
        # No session (a caller outside a turn). Report nothing stored so the
        # caller takes its "no map this turn" branch rather than dying inside a
        # route lookup that otherwise succeeded.
        logger.warning("routes_store_no_session")
        return []

    ids: list[str] = []
    try:
        for r in routes:
            row = RouteGeometry(
                id=new_route_id(),
                polyline=r.get("polyline") or "",
                label=(r.get("label") or "")[:160],
                mode=(r.get("mode") or "drive")[:16],
                distance_km=r.get("distance_km"),
                duration_min=r.get("duration_min"),
                no_traffic_min=r.get("no_traffic_min"),
                origin_lat=r.get("origin_lat"),
                origin_lng=r.get("origin_lng"),
                dest_lat=r.get("dest_lat"),
                dest_lng=r.get("dest_lng"),
                # Serialised here rather than by the caller so the column's
                # shape is owned in one place — the client contract in fetch()
                # below is the only reader.
                traffic_json=(json.dumps(r["traffic"], ensure_ascii=False)
                              if r.get("traffic") else None),
                steps_json=(json.dumps(r["steps"], ensure_ascii=False)
                            if r.get("steps") else None),
            )
            db.add(row)
            ids.append(row.id)

        # Prune here rather than in a scheduled job: it is one cheap DELETE on
        # an indexed column, and it runs exactly when the table grows.
        await db.execute(
            delete(RouteGeometry).where(
                RouteGeometry.created_at < datetime.utcnow() - timedelta(days=RETENTION_DAYS)
            )
        )
        await db.commit()
        logger.info("routes_stored", extra={"count": len(ids)})
        return ids
    except Exception as exc:  # noqa: BLE001
        logger.error("routes_store_failed", extra={"error": str(exc)})
        await db.rollback()
        return []


def _json_list(raw: str | None) -> list:
    """Stored JSON → list, never an exception. A corrupt or missing column costs
    the card its traffic colouring or its step list; it must not cost it the
    line, which is the part that actually answers the question."""
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return []
    return value if isinstance(value, list) else []


async def fetch(db: AsyncSession, route_id: str) -> dict | None:
    """One stored route, shaped for the client's map card."""
    row = (await db.execute(
        select(RouteGeometry).where(RouteGeometry.id == route_id)
    )).scalars().first()
    if row is None:
        return None
    return {
        "routeId": row.id,
        "polyline": row.polyline,
        "label": row.label or None,
        "mode": row.mode,
        "distanceKm": row.distance_km,
        "durationMin": row.duration_min,
        "noTrafficMin": row.no_traffic_min,
        # Congestion bands over the decoded polyline's point indices, and the
        # turn list. Empty lists, never null — the clients treat "no traffic
        # data" and "free-flowing" as different things and must not confuse them.
        "traffic": _json_list(row.traffic_json),
        "steps": _json_list(row.steps_json),
        "origin": ({"lat": row.origin_lat, "lng": row.origin_lng}
                   if row.origin_lat is not None else None),
        "destination": ({"lat": row.dest_lat, "lng": row.dest_lng}
                        if row.dest_lat is not None else None),
    }
