"""
Ankara bus desk (Tier 1) — live EGO "Otobüs Nerede?" arrivals by stop number.

One read-only, network-gated tool over a page EGO never exposed as an API —
see services/transit.py for the field-obfuscation/antiforgery workaround.
Unlike the navigation desk's route/place lookups, there is no map here — a
stop number is the owner's own input, the result is a short-lived arrivals
board, and no geometry is ever computed — so this renders as a ```bus fence
(a static glass list card, see prompts/core/06_visual_output.md), not a map
block. Closer in spirit to track_aircraft's status readout than to get_route,
except the whole list is the snapshot: nothing here is live-polled by the
client the way a tail number is, because a bus board goes stale in seconds
regardless and there is no single position worth animating.
"""

import json
import logging

from app.core.context import AgentContext
from app.services import transit as transit_service
from app.skills.base import Skill

logger = logging.getLogger(__name__)


class BusArrivalsSkill(Skill):
    name = "bus_arrivals"
    deferred = True
    search_keywords = (
        "ankara bus otobus otobüs ego durak stop next bus when is my bus "
        "kaç dakika nerede transit arrival eta bus stop number"
    )
    description = (
        "Looks up which Ankara EGO city buses are approaching a given stop, by "
        "the stop's number (the numeric code printed on the physical stop sign, "
        "e.g. '12219'). Use it when the owner asks when the next bus is coming, "
        "which lines pass a stop, or how far away a specific bus is, for a stop "
        "they've given you the number for. Do NOT use it for route planning "
        "between two places (use get_route with mode='transit' instead) and do "
        "NOT guess a stop number the owner hasn't given you. Returns, per line "
        "serving the stop: for buses currently live-tracked, their plate, speed, "
        "live ETA ('Geldi'=arrived, 'Gidiyor'=pulling out, or a countdown), and "
        "how far along their route they are; for lines with no bus currently "
        "inbound (or that aren't GPS-tracked), the next scheduled departure time "
        "from the first stop instead. A stop number that returns nothing was not "
        "recognised — tell the owner to double-check the number rather than "
        "assuming no buses are running. Render the result as a ```bus fence (see "
        "prompts/core/06_visual_output.md) — do NOT also retype the lines as "
        "prose; one short sentence above the fence is enough."
    )
    read_only = True
    requires_network = True
    input_schema = {
        "type": "object",
        "properties": {
            "stop_number": {
                "type": "string",
                "description": "The numeric stop code printed on the physical bus stop sign, e.g. '12219'. Required.",
            }
        },
        "required": ["stop_number"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        stop_no = str(args.get("stop_number", "")).strip()
        if not stop_no:
            return "bus_arrivals: no stop number provided."

        entries = await transit_service.stop_status(stop_no)
        if entries is None:
            return (
                f"bus_arrivals: no data for stop '{stop_no}'. Either the stop "
                "number isn't recognised by EGO, or the lookup failed — tell the "
                "owner to double-check the number rather than assuming no buses "
                "are running."
            )

        live_count = sum(1 for e in entries if e["live"])
        spec_json = json.dumps({"stopNumber": stop_no, "entries": entries}, ensure_ascii=False)

        return (
            f"BUS STOP {stop_no} — {len(entries)} line(s), {live_count} currently live-tracked.\n\n"
            "Render this as a ```bus fence, copying the JSON below EXACTLY — character "
            "for character, invent nothing, drop nothing:\n"
            f"{spec_json}\n\n"
            "One short sentence above the fence is enough (e.g. which line is closest); "
            "do NOT also retype the entries as a bulleted list below it — the card IS "
            "the answer."
        )
