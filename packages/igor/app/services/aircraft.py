"""
Live ADS-B lookup by tail number or callsign — adsb.lol's `/v2/reg/{reg}` and
`/v2/callsign/{callsign}` endpoints (ADSBExchange v2 response schema; keyless,
community-fed, unfiltered). airplanes.live served this same schema until it
started hard-403ing unregistered callers in 2026; adsb.lol is a drop-in swap,
selected via `settings.aircraft_tracking_base_url`.

No DB table and no id-store here on purpose. Unlike route/place geometry
(services/routes.py, services/places.py), which are immutable snapshots worth
protecting behind a generated id, a live aircraft position is exactly the
opposite: it changes every few seconds and a tail number/callsign is short
enough for the model/client to carry verbatim without corruption risk.
`track()` and `track_by_callsign()` are plain stateless passthroughs — the
`track_aircraft` skill calls whichever the owner gave it, and the client polls
`/aircraft/track/{tail}` (always by registration, since that's what's stable
across an aircraft's flights) on its own schedule to move a marker.
"""

import logging
import re
from urllib.parse import urlparse

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_UA = "Speda-Mark-VI/1.0 (aircraft tracking; +https://github.com/spedatox)"
_TIMEOUT = httpx.Timeout(12.0, connect=6.0)

# 7500 hijack, 7600 radio failure, 7700 general emergency — the three ICAO
# emergency squawk codes worth flagging explicitly rather than burying in a
# raw field dump.
_EMERGENCY_SQUAWKS = {"7500": "hijack", "7600": "radio failure", "7700": "general emergency"}

# ADS-B `flight` callsigns are broadcast in ICAO form (3-letter airline code,
# e.g. "UAE212"), but an owner naturally gives the IATA flight number printed
# on a boarding pass or a schedule board (2-character code, e.g. "EK212") —
# adsb.lol's callsign endpoint is an exact match and does NOT accept the IATA
# form, so a bare pass-through silently misses on almost every owner-given
# query. This is a best-effort prefix swap for major carriers a Turkey-based
# owner is likely to ask about — not exhaustive, and not authoritative where a
# carrier's ops callsign digits diverge from its commercial flight number
# (some regional/codeshare routings do). It only ever supplies a SECOND
# candidate to try; it never replaces or invents the flight-number digits the
# owner gave.
_IATA_TO_ICAO_PREFIX = {
    "TK": "THY", "PC": "PGT", "XQ": "SXS", "XC": "CAI",
    "EK": "UAE", "QR": "QTR", "EY": "ETD", "FZ": "FDB", "KU": "KAC", "SV": "SVA",
    "RJ": "RJA", "MS": "MSR", "A3": "AEE", "LH": "DLH", "BA": "BAW", "AF": "AFR",
    "KL": "KLM", "IB": "IBE", "LX": "SWR", "OS": "AUA", "AY": "FIN", "VY": "VLG",
    "DY": "NOZ", "TP": "TAP", "AZ": "ITY", "FR": "RYR", "U2": "EZY", "W6": "WZZ",
    "SU": "AFL", "AA": "AAL", "UA": "UAL", "DL": "DAL", "QF": "QFA", "SQ": "SIA",
    "NH": "ANA", "JL": "JAL", "KE": "KAL", "OZ": "AAR", "TG": "THA", "MH": "MAS",
    "AI": "AIC", "6E": "IGO", "PK": "PIA", "HY": "UZB", "J2": "AHY", "S7": "SBI",
}
_IATA_CALLSIGN_RE = re.compile(r"^([A-Z]\d|[A-Z]{2})(\d{1,4}[A-Z]?)$")


def _exc_msg(exc: Exception) -> str:
    return str(exc) or type(exc).__name__


def _source_name() -> str:
    host = urlparse(settings.aircraft_tracking_base_url).netloc
    return host or "unknown"


async def _fetch(path: str, query: str) -> dict | None:
    url = f"{settings.aircraft_tracking_base_url.rstrip('/')}{path}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers={"User-Agent": _UA}) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:
        logger.warning("aircraft_track_failed", extra={"query": query, "error": _exc_msg(exc)})
        return None
    return payload


def _parse(payload: dict, fallback_tail: str) -> dict | None:
    aircraft = payload.get("ac") or []
    if not aircraft:
        return None
    # Multiple matches are rare (a stale/duplicate registration format, or a
    # callsign reused this session) — the freshest one (lowest `seen`, seconds
    # since last message) is the live one.
    ac = min(aircraft, key=lambda a: a.get("seen", float("inf")))

    lat, lon = ac.get("lat"), ac.get("lon")
    if lat is None or lon is None:
        # Matched but no current position (e.g. only Mode-A/C, no ADS-B
        # position report yet) — nothing to put on a map.
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
        "tail": ac.get("r") or fallback_tail,
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
        "source": _source_name(),
    }


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
    payload = await _fetch(f"/v2/reg/{reg}", reg)
    if payload is None:
        return None
    return _parse(payload, fallback_tail=reg)


async def track_by_callsign(callsign: str) -> dict | None:
    """Live position + ADS-B status for a flight number/callsign (e.g. THY7RN).

    Most owners know the flight number they're on or watching, not the tail
    number painted on the aircraft — this is the lookup that lets `track_aircraft`
    answer that without making the owner go find the registration first.

    Tries the identifier exactly as given first (covers an owner or a model
    that already has the ICAO callsign). If that misses AND the string looks
    like an IATA flight number (2-char airline code + digits, e.g. "EK212"),
    retries once with the airline prefix swapped to its ICAO form via
    `_IATA_TO_ICAO_PREFIX` (e.g. "UAE212") — this is the form actually
    broadcast over ADS-B, which adsb.lol's callsign endpoint requires an exact
    match against. Same None-on-miss convention as `track()` either way; never
    fabricates a callsign outside this one known-airline substitution.
    """
    callsign = callsign.strip().upper().replace(" ", "")
    if not callsign:
        return None
    payload = await _fetch(f"/v2/callsign/{callsign}", callsign)
    result = _parse(payload, fallback_tail=callsign) if payload is not None else None
    if result is not None:
        return result

    m = _IATA_CALLSIGN_RE.match(callsign)
    if m:
        icao_prefix = _IATA_TO_ICAO_PREFIX.get(m.group(1))
        if icao_prefix:
            icao_callsign = icao_prefix + m.group(2)
            payload = await _fetch(f"/v2/callsign/{icao_callsign}", icao_callsign)
            if payload is not None:
                return _parse(payload, fallback_tail=icao_callsign)
    return None
