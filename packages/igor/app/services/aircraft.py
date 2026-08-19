"""
Live ADS-B lookup by tail number — airplanes.live's `/v2/reg/{reg}` endpoint
(ADSBExchange v2 response schema; keyless, community-fed, unfiltered).

No DB table and no id-store here on purpose. Unlike route/place geometry
(services/routes.py, services/places.py), which are immutable snapshots worth
protecting behind a generated id, a live aircraft position is exactly the
opposite: it changes every few seconds and a tail number is short enough for
the model/client to carry verbatim without corruption risk. `track()` is a
plain stateless passthrough — both the `track_aircraft` skill and the
`/aircraft/track/{tail}` router call it directly, and a client polls the
router endpoint on its own schedule to move a marker.
"""

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_UA = "Speda-Mark-VI/1.0 (aircraft tracking; +https://github.com/spedatox)"
_TIMEOUT = httpx.Timeout(12.0, connect=6.0)

# 7500 hijack, 7600 radio failure, 7700 general emergency — the three ICAO
# emergency squawk codes worth flagging explicitly rather than burying in a
# raw field dump.
_EMERGENCY_SQUAWKS = {"7500": "hijack", "7600": "radio failure", "7700": "general emergency"}


def _exc_msg(exc: Exception) -> str:
    return str(exc) or type(exc).__name__


async def track(reg: str) -> dict | None:
    """Live position + ADS-B status for a registration/tail number.

    Returns None when the API found no current match (not currently airborne,
    out of feeder coverage, or an invalid registration) or on any network/parse
    failure — callers degrade to an "unavailable" message/404 rather than
    raising, same convention as every other keyless OSINT lookup.
    """
    reg = reg.strip().upper()
    if not reg:
        return None
    url = f"{settings.aircraft_tracking_base_url.rstrip('/')}/v2/reg/{reg}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers={"User-Agent": _UA}) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:
        logger.warning("aircraft_track_failed", extra={"reg": reg, "error": _exc_msg(exc)})
        return None

    aircraft = payload.get("ac") or []
    if not aircraft:
        return None
    # Multiple matches are rare (a stale/duplicate registration format) — the
    # freshest one (lowest `seen`, seconds since last message) is the live one.
    ac = min(aircraft, key=lambda a: a.get("seen", float("inf")))

    lat, lon = ac.get("lat"), ac.get("lon")
    if lat is None or lon is None:
        # Matched the registration but no current position (e.g. only Mode-A/C,
        # no ADS-B position report yet) — nothing to put on a map.
        return None

    alt_baro = ac.get("alt_baro")
    on_ground = alt_baro == "ground"
    squawk = ac.get("squawk")
    emergency = ac.get("emergency") or "none"
    # The feed's own `emergency` field takes precedence — it can be set (e.g.
    # "general") without the squawk matching one of the three reserved codes.
    # Only fall back to the squawk-code mapping when the field itself is empty.
    emergency_kind = emergency if emergency != "none" else _EMERGENCY_SQUAWKS.get(squawk)

    return {
        "tail": ac.get("r") or reg,
        "icao24": ac.get("hex"),
        "callsign": (ac.get("flight") or "").strip() or None,
        "aircraftType": ac.get("t"),
        "lat": lat,
        "lng": lon,
        "altitudeFt": None if on_ground else alt_baro,
        "onGround": on_ground,
        "groundSpeedKt": ac.get("gs"),
        "headingDeg": ac.get("track"),
        "verticalRateFpm": ac.get("baro_rate"),
        "squawk": squawk,
        "emergency": emergency,
        "emergencyKind": emergency_kind,
        "category": ac.get("category"),
        "lastSeenSec": ac.get("seen"),
        "asOfMs": payload.get("now"),
        "source": "airplanes.live",
    }
