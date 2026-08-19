"""
Aircraft desk (Tier 1) — live ADS-B tracking by tail number.

One read-only, network-gated tool over airplanes.live's free, keyless
community feed. Unlike the navigation desk's route/place lookups, this does
NOT park anything behind a generated id — see services/aircraft.py for why a
live position is the wrong shape for that pattern. The client polls
`/aircraft/track/{tail}` directly, keyed by the tail number the model already
has, to move the marker after the initial ```aircraft fence renders it.
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
        "Looks up the live position and ADS-B status of an aircraft by its "
        "registration/tail number (e.g. N12345, TC-JJA) via airplanes.live's "
        "free, unfiltered community feed — the same kind of feed OSINT "
        "researchers use, so it includes military and government traffic that "
        "filtered commercial trackers hide. Use it when the owner asks to "
        "track, locate, or check the status of a specific plane by its tail "
        "number. Do NOT use it for commercial flight-schedule lookups like "
        "gate, delay, or ETA — it only reports live telemetry (position, "
        "altitude, speed, heading, squawk, on-ground/airborne), never airline "
        "scheduling data, and it cannot find an aircraft that is not currently "
        "broadcasting ADS-B. Returns a plain-text status summary and instructs "
        "you to render the result as an ```aircraft``` block; the client then "
        "polls the live position on its own without further tool calls."
    )
    read_only = True
    requires_network = True
    input_schema = {
        "type": "object",
        "properties": {
            "tail_number": {
                "type": "string",
                "description": "Aircraft registration/tail number, e.g. 'N12345' or 'TC-JJA'.",
            }
        },
        "required": ["tail_number"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        tail = str(args.get("tail_number", "")).strip()
        if not tail:
            return "track_aircraft: no tail number provided."

        result = await aircraft_service.track(tail)
        if result is None:
            return (
                f"track_aircraft: no live ADS-B signal for '{tail}'. It may be on "
                "the ground without its transponder on, outside feeder coverage, "
                "not currently airborne, or the registration may be wrong. Tell "
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
