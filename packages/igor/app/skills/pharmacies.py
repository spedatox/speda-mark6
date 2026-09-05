# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
On-duty pharmacy desk (Tier 1) — "nöbetçi eczane" lookup by proximity.

Backed by NosyAPI's `pharmacies-on-duty/locations` endpoint: given a
latitude/longitude it returns the nearest 20 pharmacies that are on duty RIGHT
NOW, each with name, address, phone, directions, duty start/end and distance.
Google's `openNow` flag on Places cannot answer this — a small pharmacy's duty
roster changes nightly and Google's open-hours data lags it, so "which pharmacy
is open at 3am" is exactly the question the official roster answers and Google
does not.

Origin resolution mirrors navigation.py's "from me / from home" pattern: the
owner's live location (stamped onto AgentContext.extra by the chat router) →
their configured home → an explicit {lat,lng} the model passes. No free-text
geocoding here on purpose: NosyAPI needs a point, not a name, and the live/home
chain already answers the real question ("near me") without dragging in a second
geocoder. Results are parked in the place store and handed back as a ```map
fence carrying a placesId, exactly like find_places — the client already renders
that card, so there is no new client work.
"""

import json
import logging

import httpx

from app.config import settings
from app.core.context import AgentContext
from app.services import places as place_store
from app.skills.base import Skill

logger = logging.getLogger(__name__)

_LOCATIONS_URL = "https://www.nosyapi.com/apiv2/service/pharmacies-on-duty/locations"
_TIMEOUT = httpx.Timeout(12.0, connect=6.0)
# The endpoint always returns the nearest 20; the card and digest keep the top N.
_DEFAULT_RESULTS = 10
_MAX_RESULTS = 20


def _client_location(context: AgentContext) -> dict | None:
    """The owner's live location for this turn, if the client shared it."""
    loc = context.extra.get("client_location")
    if isinstance(loc, dict) and "lat" in loc and "lng" in loc:
        try:
            return {"lat": float(loc["lat"]), "lng": float(loc["lng"])}
        except (TypeError, ValueError):
            return None
    return None


def _home_location() -> dict | None:
    # Same defensive coercion as navigation.py: a value set live from the
    # Configuration tab arrives as a string, a value from .env as a float.
    lat, lng = settings.owner_home_lat, settings.owner_home_lng
    if lat in (None, "") or lng in (None, ""):
        return None
    try:
        return {"lat": float(lat), "lng": float(lng)}
    except (TypeError, ValueError):
        return None


def _resolve_center(value, context: AgentContext) -> tuple[dict | None, str]:
    """Search centre → ({lat,lng} | None, reason).

    Accepts a {lat,lng} object, a JSON-object STRING, or — when empty — the
    owner's live location, then their configured home. Free text is NOT
    geocoded: the model is told to omit `near` and let the live/home chain do
    the work, and a name it insists on resolves to "invalid" rather than being
    sent to a second provider.
    """
    if isinstance(value, dict) and "lat" in value and "lng" in value:
        try:
            return {"lat": float(value["lat"]), "lng": float(value["lng"])}, "ok"
        except (TypeError, ValueError):
            return None, "invalid"
    text = (value or "").strip() if isinstance(value, str) else ""
    if text:
        # A coordinate object may arrive as a JSON string — parse it before
        # treating the text as unusable (same shape navigation.py accepts).
        if text.startswith("{"):
            try:
                parsed = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                parsed = None
            if isinstance(parsed, dict) and "lat" in parsed and "lng" in parsed:
                try:
                    return {"lat": float(parsed["lat"]), "lng": float(parsed["lng"])}, "ok"
                except (TypeError, ValueError):
                    return None, "invalid"
        return None, "invalid"
    hit = _client_location(context) or _home_location()
    return (hit, "ok" if hit else "missing")


def _duty_window(start, end) -> str:
    """'2024-01-21 09:00:00' → '2024-01-21 09:00'. Dates kept verbatim — the
    card may localize them later; re-parsing here would only risk a wrong
    timezone for no gain."""
    def trim(s):
        return s[:16] if isinstance(s, str) and len(s) >= 16 else (s or "")
    return f"{trim(start)} → {trim(end)}"


def _maps_place_link(lat: float, lng: float) -> str:
    """A Google Maps search link by coordinates — unambiguous, no URL-encoding
    of Turkish names required, and it lets the owner tap through to navigate."""
    return f"https://www.google.com/maps/search/?api=1&query={lat},{lng}"


def _pharmacy_record(p: dict, centre: dict | None) -> dict | None:
    """One NosyAPI result → the stored client record (services/places.py shape).

    None when there are no coordinates — a pharmacy we cannot put on the map is
    not a map result. The generic card fields (name/lat/lng/address/phone/hours/
    distanceKm) are populated so the existing ```map card renders it unchanged;
    pharmacy-specific fields ride along for any future client that grows aware of
    them, and the client ignores what it does not know today.
    """
    lat, lng = p.get("latitude"), p.get("longitude")
    if lat is None or lng is None:
        return None

    rec: dict = {
        "name": p.get("pharmacyName") or "(isimsiz eczane)",
        "lat": float(lat),
        "lng": float(lng),
        "category": "Nöbetçi Eczane",
        "openNow": True,  # on duty by definition — the endpoint lists only those
        "mapsUri": _maps_place_link(float(lat), float(lng)),
        "pharmacyId": p.get("pharmacyID"),
    }

    address = p.get("address")
    if address:
        rec["address"] = address

    phone = p.get("phone")
    phone2 = p.get("phone2")
    if phone2:
        phone = f"{phone} / {phone2}" if phone else phone2
    if phone:
        rec["phone"] = phone

    directions = p.get("directions")
    if directions:
        rec["directions"] = directions

    duty_start, duty_end = p.get("pharmacyDutyStart"), p.get("pharmacyDutyEnd")
    if duty_start and duty_end:
        rec["dutyStart"] = duty_start
        rec["dutyEnd"] = duty_end
        # The duty window IS this pharmacy's "opening hours right now", so it goes
        # in the hours field the card already renders rather than inventing a new
        # slot the client would ignore.
        rec["hours"] = [f"Nöbet: {_duty_window(duty_start, duty_end)}"]

    dkm = p.get("distanceKm")
    if dkm is not None:
        try:
            rec["distanceKm"] = round(float(dkm), 1)
        except (TypeError, ValueError):
            pass
    elif centre is not None:
        rec["distanceKm"] = round(
            place_store.haversine_km(centre["lat"], centre["lng"], float(lat), float(lng)), 1
        )

    return rec


class OnDutyPharmacySkill(Skill):
    name = "on_duty_pharmacies"
    deferred = True
    search_keywords = (
        "nöbetçi eczane eczane pharmacy on duty night pharmacy açık eczane "
        "gece eczanesi nöbet en yakın eczane"
    )
    description = (
        "Finds the pharmacies on duty RIGHT NOW near a point, from the official "
        "Turkish nöbetçi eczane roster (NosyAPI), to answer 'which pharmacy is "
        "open now', 'en yakın nöbetçi eczane', 'gece açık eczane'. Use it whenever "
        "the owner asks for an on-duty pharmacy — NOT find_places, because Google's "
        "open-now flag lags the nightly duty roster and will miss the one that is "
        "actually open. The search centres on the owner's live location, then their "
        "configured home, so 'near me' needs no coordinates. Returns each pharmacy's "
        "name, distance and duty window (start → end) as a digest, plus a placesId "
        "to put into a ```map fence — the client draws tappable markers carrying the "
        "full record (address, phone, directions, duty hours) and a navigate action, "
        "none of which you need to transcribe."
    )
    read_only = True
    requires_network = True
    input_schema = {
        "type": "object",
        "properties": {
            "near": {
                "type": "string",
                "description": (
                    "Centre point as a JSON string like '{\"lat\":40.18,\"lng\":28.74}'. "
                    "OMIT to use the owner's current live location (falls back to their "
                    "configured home). Free-text place names are not supported — leave it "
                    "empty for 'near me'."
                ),
            },
            "max_results": {
                "type": "integer",
                "description": "How many pharmacies to return (1-20, default 10).",
                "default": 10,
            },
        },
        "required": [],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        if not settings.nosyapi_api_key:
            return (
                "on_duty_pharmacies is unavailable — no NosyAPI key is configured "
                "(NOSYAPI_API_KEY). Tell the owner to add it in Settings → "
                "Configuration → Nöbetçi Eczane rather than guessing which pharmacy "
                "is open."
            )
        max_results = min(max(int(args.get("max_results", _DEFAULT_RESULTS) or _DEFAULT_RESULTS), 1), _MAX_RESULTS)

        centre, why = _resolve_center(args.get("near"), context)
        if centre is None:
            if why == "invalid":
                return (
                    "on_duty_pharmacies: 'near' must be coordinates (a JSON string "
                    "like '{\"lat\":40.18,\"lng\":28.74}'), not a place name. Omit it "
                    "to use the owner's live location or configured home instead."
                )
            return (
                "on_duty_pharmacies: no location to search from — the owner didn't "
                "share one this turn and no home is configured. Ask where they are, "
                "or have them set their home location."
            )

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    _LOCATIONS_URL,
                    params={
                        "latitude": centre["lat"],
                        "longitude": centre["lng"],
                    },
                    headers={"X-NSYP": settings.nosyapi_api_key},
                )
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            return f"on_duty_pharmacies: couldn't reach NosyAPI ({type(e).__name__})."
        except Exception as e:  # noqa: BLE001
            return f"on_duty_pharmacies: request failed ({type(e).__name__}: {e})."

        if resp.status_code != 200:
            return (
                f"on_duty_pharmacies: NosyAPI returned HTTP {resp.status_code}. "
                f"{resp.text[:200]}"
            )

        try:
            payload = resp.json()
        except ValueError:
            return "on_duty_pharmacies: NosyAPI returned a non-JSON response."

        if payload.get("status") != "success":
            message = payload.get("message") or payload.get("messageTR") or "unknown error"
            logger.warning("nosyapi_refused", extra={"detail": message})
            return (
                f"on_duty_pharmacies: NosyAPI refused the request — {message}. This is "
                "usually a bad or expired API key; tell the owner to check it in "
                "Settings → Configuration, not to rephrase the question."
            )

        data = payload.get("data") or []
        records = [_pharmacy_record(p, centre) for p in data]
        records = [r for r in records if r is not None]
        # NosyAPI returns nearest-first, but sort defensively so a change on their
        # side can't hand us an unsorted list.
        records.sort(key=lambda r: r.get("distanceKm") if r.get("distanceKm") is not None else float("inf"))
        records = records[:max_results]

        if not records:
            return (
                f"on_duty_pharmacies: no on-duty pharmacies near "
                f"[{centre['lat']:.4f},{centre['lng']:.4f}]. This is unusual — the "
                "roster may be mid-update; tell the owner to try again shortly or "
                "check a wider area."
            )

        set_id = await place_store.store(context.db, "nöbetçi eczane", centre, records)

        lines = [
            f"PHARMACIES — {len(records)} on-duty near [{centre['lat']:.5f},{centre['lng']:.5f}]:"
        ]
        for r in records:
            bits = []
            if r.get("distanceKm") is not None:
                bits.append(f"{r['distanceKm']} km")
            if r.get("dutyStart") and r.get("dutyEnd"):
                bits.append(f"nöbet {_duty_window(r['dutyStart'], r['dutyEnd'])}")
            lines.append(f"- {r['name']}" + (f" — {' · '.join(bits)}" if bits else ""))

        if set_id:
            lines.append(
                "\nRender this as a ```map fence carrying the whole result set by id — "
                f'copy it exactly:\n  "places": "{set_id}"\n'
                "The client draws every pharmacy as a tappable marker and resolves the "
                "full record itself (address, phone, directions, duty hours, and a "
                "per-place NAVIGATE), so do NOT write a markers array for these and do "
                "NOT retype their details into the fence or a list below it. Your prose "
                "should say which one you'd pick and why — one or two sentences — "
                "because the browsing is what the card is for."
            )
        else:
            lines.append(
                "\nThe result set could NOT be stored, so there is no placesId this turn. "
                "Write the markers yourself in the ```map fence (kind='poi', name as label, "
                "duty window as subtitle) using these coordinates exactly:"
            )
            for r in records:
                lines.append(f"- {r['name']} [{r['lat']:.6f},{r['lng']:.6f}]")
        return "\n".join(lines)
