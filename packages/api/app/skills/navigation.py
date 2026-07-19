"""
Navigation desk skills (Tier 1) — traffic-aware routing and place search.

Two tools over the Google Maps Platform, both read-only and network-gated:
  - get_route    — A→B directions with LIVE traffic, alternatives, encoded
                   polylines (Routes API v2)
  - find_places  — "where can I go" POI search near a point (Places API New)

The design intent — encoded in the descriptions so the model routes correctly —
is: NEVER recite raw coordinates to the owner. Call get_route / find_places to
get real geometry, then render a ```map fence (see prompts/core/06_visual_output).
The client turns the fence into an inline Stark map and a one-tap Google Maps
handoff. The API key lives here only; clients never see it.

Origin defaults to the owner's live location (AgentContext.extra["client_location"],
stamped by the chat router) and falls back to owner_home_* from config — so
"how do I get home?" works from any surface without the model re-asking where
the owner is.
"""

import json
import logging
from typing import Any

import httpx

from app.config import settings
from app.core.context import AgentContext
from app.skills.base import Skill

logger = logging.getLogger(__name__)

_ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
_PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
_TIMEOUT = httpx.Timeout(12.0, connect=6.0)

# Routes API travel modes ← our compact vocabulary.
_TRAVEL_MODE = {
    "drive": "DRIVE",
    "walk": "WALK",
    "transit": "TRANSIT",
    "two_wheeler": "TWO_WHEELER",
    "bicycle": "BICYCLE",
}


def _client_location(context: AgentContext) -> dict | None:
    """The owner's live location for this turn, if the client shared it.
    Stamped onto context.extra by the chat router (never persisted)."""
    loc = context.extra.get("client_location")
    if isinstance(loc, dict) and "lat" in loc and "lng" in loc:
        return {"lat": float(loc["lat"]), "lng": float(loc["lng"])}
    return None


def _home_location() -> dict | None:
    # Coerce defensively: a value set live from the Configuration tab arrives as a
    # string, while a value from .env is already a float — either must end up float
    # so the request body and the coordinate formatting can't choke.
    lat, lng = settings.owner_home_lat, settings.owner_home_lng
    if lat in (None, "") or lng in (None, ""):
        return None
    try:
        return {"lat": float(lat), "lng": float(lng)}
    except (TypeError, ValueError):
        return None


async def _geocode(client: httpx.AsyncClient, address: str) -> dict | None:
    """Free-text → {lat,lng,place} via the Geocoding API. None on no match."""
    resp = await client.get(
        _GEOCODE_URL,
        params={"address": address, "key": settings.google_maps_api_key},
    )
    if resp.status_code != 200:
        return None
    results = resp.json().get("results") or []
    if not results:
        return None
    top = results[0]
    loc = top["geometry"]["location"]
    return {"lat": loc["lat"], "lng": loc["lng"], "place": top.get("formatted_address")}


async def _resolve_point(
    client: httpx.AsyncClient, value: Any, context: AgentContext, *, allow_defaults: bool
) -> dict | None:
    """A route/search endpoint → {lat,lng,place?}.

    Accepts a {lat,lng} object (used verbatim), a JSON-object STRING like
    '{"lat":..,"lng":..}' (the schema's coordinate form — the model passes strings),
    a free-text address (geocoded), or — when allow_defaults and the value is empty
    — the owner's live location, then their configured home.
    """
    if isinstance(value, dict) and "lat" in value and "lng" in value:
        return {"lat": float(value["lat"]), "lng": float(value["lng"]),
                "place": value.get("place")}
    text = (value or "").strip() if isinstance(value, str) else ""
    if text:
        # A coordinate object may arrive as a JSON string — parse it before
        # falling back to treating the text as a geocodable address.
        if text.startswith("{"):
            parsed = None
            try:
                parsed = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                parsed = None
            if isinstance(parsed, dict) and "lat" in parsed and "lng" in parsed:
                return {"lat": float(parsed["lat"]), "lng": float(parsed["lng"]),
                        "place": parsed.get("place")}
        return await _geocode(client, text)
    if allow_defaults:
        return _client_location(context) or _home_location()
    return None


class GetRouteSkill(Skill):
    name = "get_route"
    description = (
        "Computes turn-by-turn driving/walking/transit directions between two "
        "points WITH LIVE TRAFFIC, using Google's Routes API. Use it whenever the "
        "owner asks how to get somewhere, how long a trip will take right now, "
        "which way is fastest, or to compare routes ('en hızlı yol', 'how do I "
        "get home', 'trafik nasıl') — then render the result as a ```map fence, "
        "never as spoken coordinates. Do NOT use it to merely show where a single "
        "place IS (that needs no routing) or to search for places (use "
        "find_places). Origin may be omitted to route from the owner's current "
        "location automatically. Returns, per route: distance, live-traffic "
        "duration AND the no-traffic duration (their gap is the congestion story), "
        "the road summary, an encoded polyline for drawing, and a ready Google "
        "Maps deep link — plus the resolved origin/destination coordinates so you "
        "can build the fence without asking again."
    )
    read_only = True
    requires_network = True
    input_schema = {
        "type": "object",
        "properties": {
            "destination": {
                "type": "string",
                "description": "Where to go: a free-text address/place name (geocoded) OR a JSON string like '{\"lat\":41.1,\"lng\":29.0}'. Required.",
            },
            "origin": {
                "type": "string",
                "description": "Where from. Same forms as destination. OMIT to route from the owner's current live location (falls back to their configured home).",
            },
            "mode": {
                "type": "string",
                "enum": ["drive", "walk", "transit", "two_wheeler", "bicycle"],
                "description": "Travel mode. Default 'drive'.",
                "default": "drive",
            },
            "alternatives": {
                "type": "boolean",
                "description": "Ask for up to 2 alternative routes as well as the primary. Default true.",
                "default": True,
            },
        },
        "required": ["destination"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        if not settings.google_maps_api_key:
            return (
                "get_route is unavailable — no Google Maps key is configured "
                "(GOOGLE_MAPS_API_KEY). Tell the owner routing isn't set up rather "
                "than reciting coordinates."
            )
        mode = (args.get("mode") or "drive").strip().lower()
        travel_mode = _TRAVEL_MODE.get(mode, "DRIVE")
        want_alts = bool(args.get("alternatives", True))

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                origin = await _resolve_point(client, args.get("origin"), context, allow_defaults=True)
                dest = await _resolve_point(client, args.get("destination"), context, allow_defaults=False)
                if origin is None:
                    return ("get_route: no origin — the owner didn't share a location this "
                            "turn and no home is configured. Ask where to start from.")
                if dest is None:
                    return f"get_route: couldn't resolve the destination '{args.get('destination')}'."

                body = {
                    "origin": {"location": {"latLng": {"latitude": origin["lat"], "longitude": origin["lng"]}}},
                    "destination": {"location": {"latLng": {"latitude": dest["lat"], "longitude": dest["lng"]}}},
                    "travelMode": travel_mode,
                    "computeAlternativeRoutes": want_alts,
                }
                # Traffic-aware timing only applies to motorised modes.
                if travel_mode in ("DRIVE", "TWO_WHEELER"):
                    body["routingPreference"] = "TRAFFIC_AWARE_OPTIMAL"

                field_mask = (
                    "routes.duration,routes.staticDuration,routes.distanceMeters,"
                    "routes.polyline.encodedPolyline,routes.description,routes.routeLabels"
                )
                resp = await client.post(
                    _ROUTES_URL,
                    json=body,
                    headers={
                        "X-Goog-Api-Key": settings.google_maps_api_key,
                        "X-Goog-FieldMask": field_mask,
                        "Content-Type": "application/json",
                    },
                )
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            return f"get_route: couldn't reach Google Routes ({type(e).__name__})."
        except Exception as e:  # noqa: BLE001
            return f"get_route: request failed ({type(e).__name__}: {e})."

        if resp.status_code != 200:
            detail = resp.text[:200]
            return f"get_route: Routes API returned HTTP {resp.status_code}. {detail}"

        routes = resp.json().get("routes") or []
        if not routes:
            return (f"get_route: no route found from {_label(origin)} to {_label(dest)} "
                    f"by {mode}. It may be unreachable in this mode.")

        deep_link = _maps_dir_link(origin, dest, mode)
        lines = [
            f"ROUTE {_label(origin)} → {_label(dest)} · mode={mode} · {len(routes)} option(s)",
            f"origin=[{origin['lat']:.6f},{origin['lng']:.6f}] "
            f"destination=[{dest['lat']:.6f},{dest['lng']:.6f}]",
            f"google_maps_link={deep_link}",
            "",
        ]
        for i, r in enumerate(routes):
            dur = _dur_min(r.get("duration"))
            static = _dur_min(r.get("staticDuration"))
            dist_km = round((r.get("distanceMeters") or 0) / 1000.0, 1)
            labels = ", ".join(r.get("routeLabels") or []) or ("primary" if i == 0 else "alternative")
            summary = r.get("description") or "—"
            delay = (f", +{dur - static} min vs no-traffic" if dur is not None and static is not None
                     and dur > static else "")
            poly = (r.get("polyline") or {}).get("encodedPolyline", "")
            lines.append(
                f"[{i}] {labels}: {dist_km} km, ~{dur if dur is not None else '?'} min{delay}"
                f" via {summary}"
            )
            lines.append(f"    polyline={poly}")
        lines.append(
            "\nRender this as a ```map fence: one route per polyline (mark route 0 primary), "
            "include durationMin AND noTrafficMin so the client shows the traffic delta, and "
            "set navigate to the destination so the owner can tap through to Google Maps."
        )
        return "\n".join(lines)


class FindPlacesSkill(Skill):
    name = "find_places"
    description = (
        "Searches for places (cafés, pharmacies, gas stations, restaurants, ATMs, "
        "…) near a point using Google's Places API, to answer 'where can I go for "
        "X', 'nearest Y', 'best Z around here'. Use it to gather real candidates — "
        "name, location, rating, whether it's open now, address — then render them "
        "as markers in a ```map fence and reason about the best options; do NOT "
        "recite a bare list of coordinates. Do NOT use it for directions to a known "
        "place (use get_route). Search centres on the owner's current location by "
        "default. Returns up to the requested number of places with lat/lng, "
        "rating, open-now status, price level and address — everything needed to "
        "drop map markers and recommend."
    )
    read_only = True
    requires_network = True
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to look for, e.g. 'specialty coffee', 'eczane', '24 saat açık benzin istasyonu'. Required.",
            },
            "near": {
                "type": "string",
                "description": "Centre point as '{\"lat\":..,\"lng\":..}' or an address. OMIT to use the owner's current location.",
            },
            "open_now": {
                "type": "boolean",
                "description": "Only return places open right now. Default false.",
                "default": False,
            },
            "max_results": {
                "type": "integer",
                "description": "How many places to return (1–20, default 8).",
                "default": 8,
            },
        },
        "required": ["query"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        if not settings.google_maps_api_key:
            return ("find_places is unavailable — no Google Maps key is configured "
                    "(GOOGLE_MAPS_API_KEY).")
        query = (args.get("query") or "").strip()
        if not query:
            return "find_places: a 'query' is required."
        max_results = min(max(int(args.get("max_results", 8) or 8), 1), 20)
        open_now = bool(args.get("open_now", False))

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                centre = await _resolve_point(client, args.get("near"), context, allow_defaults=True)
                body: dict = {"textQuery": query, "maxResultCount": max_results, "openNow": open_now}
                if centre is not None:
                    body["locationBias"] = {
                        "circle": {
                            "center": {"latitude": centre["lat"], "longitude": centre["lng"]},
                            "radius": 5000.0,
                        }
                    }
                field_mask = (
                    "places.displayName,places.formattedAddress,places.location,"
                    "places.rating,places.userRatingCount,places.currentOpeningHours.openNow,"
                    "places.priceLevel,places.googleMapsUri"
                )
                resp = await client.post(
                    _PLACES_URL,
                    json=body,
                    headers={
                        "X-Goog-Api-Key": settings.google_maps_api_key,
                        "X-Goog-FieldMask": field_mask,
                        "Content-Type": "application/json",
                    },
                )
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            return f"find_places: couldn't reach Google Places ({type(e).__name__})."
        except Exception as e:  # noqa: BLE001
            return f"find_places: request failed ({type(e).__name__}: {e})."

        if resp.status_code != 200:
            return f"find_places: Places API returned HTTP {resp.status_code}. {resp.text[:200]}"

        places = resp.json().get("places") or []
        if not places:
            return f"find_places: no results for '{query}'" + (
                f" near [{centre['lat']:.4f},{centre['lng']:.4f}]." if centre else ".")

        where = f" near [{centre['lat']:.5f},{centre['lng']:.5f}]" if centre else ""
        lines = [f"PLACES — {len(places)} result(s) for '{query}'{where}:"]
        for p in places:
            name = (p.get("displayName") or {}).get("text", "(unnamed)")
            loc = p.get("location") or {}
            lat, lng = loc.get("latitude"), loc.get("longitude")
            rating = p.get("rating")
            reviews = p.get("userRatingCount")
            opn = (p.get("currentOpeningHours") or {}).get("openNow")
            price = p.get("priceLevel")
            addr = p.get("formattedAddress", "")
            bits = []
            if rating is not None:
                bits.append(f"{rating}★" + (f" ({reviews})" if reviews else ""))
            if opn is True:
                bits.append("open now")
            elif opn is False:
                bits.append("closed")
            if price:
                bits.append(str(price).replace("PRICE_LEVEL_", "").lower())
            meta = " · ".join(bits)
            coord = f"[{lat:.6f},{lng:.6f}]" if lat is not None and lng is not None else "[?]"
            lines.append(f"- {name} {coord}{(' — ' + meta) if meta else ''}\n  {addr}")
        lines.append("\nDrop these as ```map markers (kind='poi', put the rating/open state in "
                     "subtitle) and recommend the best fit.")
        return "\n".join(lines)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _label(pt: dict) -> str:
    return pt.get("place") or f"[{pt['lat']:.4f},{pt['lng']:.4f}]"


def _dur_min(dur: Any) -> int | None:
    """Routes API durations are strings like '1234s'. → whole minutes."""
    if not dur or not isinstance(dur, str) or not dur.endswith("s"):
        return None
    try:
        return round(int(dur[:-1]) / 60)
    except ValueError:
        return None


def _maps_dir_link(origin: dict, dest: dict, mode: str) -> str:
    travel = {"drive": "driving", "walk": "walking", "transit": "transit",
              "two_wheeler": "driving", "bicycle": "bicycling"}.get(mode, "driving")
    return (
        "https://www.google.com/maps/dir/?api=1"
        f"&origin={origin['lat']},{origin['lng']}"
        f"&destination={dest['lat']},{dest['lng']}"
        f"&travelmode={travel}"
    )
