"""
Aircraft desk (Tier 1) — live ADS-B tracking by tail number or callsign.

One read-only, network-gated tool over adsb.lol's free, keyless community
feed (see services/aircraft.py for why airplanes.live, the original source,
is no longer the default). Unlike the navigation desk's route/place lookups,
this does NOT park anything behind a generated id — see services/aircraft.py
for why a live position is the wrong shape for that pattern. The client polls
`/aircraft/track/{tail}` directly, keyed by the registration the lookup
resolved to (returned in the result even when the owner gave a callsign), to
move the marker after the initial ```aircraft fence renders it.
"""

import logging

from app.core.context import AgentContext
from app.services import aircraft as aircraft_service
from app.skills.base import Skill

logger = logging.getLogger(__name__)


class TrackAircraftSkill(Skill):
    name = "track_aircraft"
    deferred = True
    search_keywords = (
        "plane aircraft flight tracking tail number registration ADS-B OSINT "
        "airplanes.live squawk hijack transponder live position where is this plane"
    )
    description = (
        "Looks up the live position and ADS-B status of an aircraft, either by "
        "its registration/tail number (e.g. N12345, TC-JJA) or by its flight "
        "number/callsign (e.g. THY7RN, PC5555), via adsb.lol's free, "
        "unfiltered community feed — the same kind of feed OSINT researchers "
        "use, so it includes military and government traffic that filtered "
        "commercial trackers hide. Prefer whichever identifier the owner "
        "actually gave you: most people know a flight number, not a tail "
        "number, and this tool does NOT need to ask for the registration if a "
        "callsign is available — pass tail_number when you have it, otherwise "
        "pass flight_number. Do NOT use it for commercial flight-schedule "
        "lookups like gate, delay, or ETA — it only reports live telemetry "
        "(position, altitude, speed, heading, squawk, on-ground/airborne), "
        "never airline scheduling data, and it cannot find an aircraft that is "
        "not currently broadcasting ADS-B. Returns a plain-text status summary "
        "and instructs you to render the result as an ```aircraft``` block; "
        "the client then polls the live position on its own without further "
        "tool calls."
    )
    read_only = True
    requires_network = True
    input_schema = {
        "type": "object",
        "properties": {
            "tail_number": {
                "type": "string",
                "description": "Aircraft registration/tail number, e.g. 'N12345' or 'TC-JJA'. Use this when you have it.",
            },
            "flight_number": {
                "type": "string",
                "description": (
                    "Flight number/callsign, e.g. 'THY7RN' or 'PC5555'. Use this instead of "
                    "tail_number when that's what the owner gave you — do not ask them to look "
                    "up the registration first."
                ),
            },
        },
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        tail = str(args.get("tail_number", "")).strip()
        flight = str(args.get("flight_number", "")).strip()
        query = tail or flight
        if not query:
            return "track_aircraft: no tail number or flight number provided."

        result = await aircraft_service.track(tail) if tail else await aircraft_service.track_by_callsign(flight)
        if result is None:
            return (
                f"track_aircraft: no live ADS-B signal for '{query}'. It may be on "
                "the ground without its transponder on, outside feeder coverage, "
                "not currently airborne, or the identifier may be wrong. Tell "
                "the owner it isn't currently trackable rather than guessing a "
                "position."
            )

        alt = "on the ground" if result["onGround"] else f"{result['altitudeFt']:,} ft" if result["altitudeFt"] is not None else "altitude unknown"
        lines = [
            f"AIRCRAFT {result['tail']}" + (f" ({result['callsign']})" if result["callsign"] else ""),
            f"- Type: {result['aircraftType'] or 'unknown'}  ·  ICAO24: {result['icao24'] or '?'}",
            f"- Position: [{result['lat']:.5f},{result['lng']:.5f}]  ·  {alt}",
        ]
        if not result["onGround"]:
            lines.append(
                f"- Speed: {result['groundSpeedKt'] if result['groundSpeedKt'] is not None else '?'} kt  "
                f"·  Heading: {result['headingDeg'] if result['headingDeg'] is not None else '?'}°  "
                f"·  Vertical rate: {result['verticalRateFpm'] if result['verticalRateFpm'] is not None else '?'} ft/min"
            )
        lines.append(f"- Squawk: {result['squawk'] or '?'}  ·  Last seen: {result['lastSeenSec']}s ago")

        if result["emergencyKind"]:
            lines.append(
                f"- ⚠ EMERGENCY SQUAWK: {result['squawk']} ({result['emergencyKind']}). "
                "Flag this to the owner explicitly — do not bury it in the summary."
            )

        lines.append(
            "\nRender this as an ```aircraft fence, copying these fields exactly "
            "(the client polls /aircraft/track/{tail} on its own from here — "
            "do not call this tool again just to refresh the position):\n"
            "  {\"tail\": \"" + result["tail"] + "\", "
            "\"icao24\": \"" + (result["icao24"] or "") + "\", "
            "\"callsign\": " + (f'"{result["callsign"]}"' if result["callsign"] else "null") + ", "
            "\"aircraftType\": " + (f'"{result["aircraftType"]}"' if result["aircraftType"] else "null") + ", "
            f"\"lat\": {result['lat']}, \"lng\": {result['lng']}, "
            "\"altitudeFt\": " + (str(result["altitudeFt"]) if result["altitudeFt"] is not None else "null") + ", "
            f"\"onGround\": {str(result['onGround']).lower()}, "
            "\"groundSpeedKt\": " + (str(result["groundSpeedKt"]) if result["groundSpeedKt"] is not None else "null") + ", "
            "\"headingDeg\": " + (str(result["headingDeg"]) if result["headingDeg"] is not None else "null") + ", "
            "\"squawk\": " + (f'"{result["squawk"]}"' if result["squawk"] else "null") + "}"
        )
        return "\n".join(lines)
