"""
EGO "Otobüs Nerede?" scrape — Ankara's public-bus arrival board by stop number.

EGO (ego.gov.tr) has no public API for this, and the form turned out to need
more than a plain client. Its stop-number <input>'s `name`/`id` is a random
numeric string regenerated on every page load, paired with an ASP.NET
`__RequestVerificationToken` — findable by reading the form's own markup on a
fresh GET, and that part a bare `httpx` client handles fine. What it does NOT
get past is the WAF sitting in front of the POST (an F5-style `TS...` cookie
shows up on every response): a raw client's GET+POST round-trip was rejected
outright in testing, every time, including with the exact field name and
token the page had just handed it, while the SAME flow driven through a real
browser succeeded every time. So this goes through the Playwright sidecar
(services/browser.py's `act()`) rather than hand-rolling the antiforgery
dance — the sidecar's browser fingerprint is what actually clears the WAF,
not the request shape.

`act()` only hands back rendered TEXT (services/browser.py has no raw-HTML
passthrough), so parsing works off the page's flattened text rather than its
`.bus-card` markup. Each result renders as one of two fixed-length line
groups, in order:
  - LIVE (most urban lines) — 5 lines: line code, route title, "PLATE,
    [ROUTE_CODE], Hız:N km, TAGS", the ETA ("Geldi"=arrived / "Gidiyor"=
    pulling out / "N sn" / "N dk"), and "X/Y" — the bus's current stop index
    over the total stops on its route (a progress marker, not occupancy).
  - SCHEDULE-ONLY (no vehicle currently inbound, or a rural/ÖHO line that may
    not carry GPS at all) — 3 lines: line code, route title, "Sonraki
    Hareket Saati İlk Duraktan HH:MM / <duration> Sonra".
A line code always matches `_LINE_RE`; nothing else in either group does, so
grouping by "does this line look like a line code" is what tells the two
apart and finds each group's boundary. Zero groups found means the stop
number itself was not recognised — the page shows no separate error message
for that case.
"""

import logging
import re

from app.services import browser as browser_service

logger = logging.getLogger(__name__)

_FORM_URL = "https://www.ego.gov.tr/tr/otobusnerede"
_INPUT_SELECTOR = "form.bus-form input[type=text]"
_SUBMIT_SELECTOR = "form.bus-form button[type=submit]"

_LINE_RE = re.compile(r"^\d{2,4}(-\d{1,2})?$")
_META_RE = re.compile(
    r"^(?P<plate>[^,]+),\s*\[(?P<code>[^\]]+)\],\s*Hız:\s*(?P<speed>\d+)\s*km,\s*(?P<tags>.+)$"
)
_SCHEDULE_RE = re.compile(r"(\d{2}:\d{2})\s*/\s*(.+?)\s*Sonra")


def _parse_text(page_text: str) -> list[dict]:
    lines = [ln.strip() for ln in page_text.splitlines() if ln.strip()]
    # Results start right after the LAST of the (repeated, breadcrumb/heading/
    # button) "Otobüs Nerede?" lines and end at the diagnostic footer — an
    # IPv4 address on its own line, immediately before a timestamp — that this
    # page appends after every query. Anything before or after is chrome.
    start = end = None
    for i, ln in enumerate(lines):
        if ln == "Otobüs Nerede?":
            start = i + 1
        elif start is not None and re.match(r"^\d{1,3}(\.\d{1,3}){3}$", ln):
            end = i
            break
    if start is None:
        return []
    block = lines[start:end]

    entries: list[dict] = []
    i = 0
    while i < len(block):
        if not _LINE_RE.match(block[i]):
            i += 1
            continue
        line_code = block[i]
        if i + 1 >= len(block):
            break
        route = block[i + 1]
        if i + 2 >= len(block):
            break
        third = block[i + 2]

        if "Hız:" in third:
            if i + 4 >= len(block):
                break
            meta_m = _META_RE.match(third)
            eta, queue = block[i + 3], block[i + 4]
            stop_idx = total_stops = None
            if "/" in queue:
                a, _, b = queue.partition("/")
                try:
                    stop_idx, total_stops = int(a), int(b)
                except ValueError:
                    pass
            tags: list[str] = []
            plate = route_code = None
            speed_kmh = None
            if meta_m:
                plate = meta_m.group("plate").strip()
                route_code = meta_m.group("code").strip()
                speed_kmh = int(meta_m.group("speed"))
                # The source separates tags with U+201A (‚), not a comma.
                tags = [
                    t.strip()
                    for t in meta_m.group("tags").replace("\u201a", ",").split(",")
                    if t.strip() and t.strip() != "-"
                ]
            entries.append({
                "line": line_code, "route": route, "live": True,
                "plate": plate, "routeCode": route_code, "speedKmh": speed_kmh,
                "tags": tags, "eta": eta,
                "stopIndex": stop_idx, "totalStops": total_stops,
            })
            i += 5
        elif third.startswith("Sonraki Hareket Saati"):
            m = _SCHEDULE_RE.search(third)
            entries.append({
                "line": line_code, "route": route, "live": False,
                "nextDeparture": m.group(1) if m else None,
                "inWords": m.group(2) if m else None,
            })
            i += 3
        else:
            logger.warning("ego_bus_unrecognised_line", extra={"line": line_code, "third": third[:120]})
            i += 1
    return entries


def _eta_key(e: dict) -> tuple:
    if not e["live"]:
        return (2, 0)
    eta = e["eta"]
    if eta == "Geldi":
        return (0, 0)
    m = re.match(r"(\d+)\s*(sn|dk)", eta)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        return (0, n if unit == "sn" else n * 60)
    return (1, 0)  # e.g. "Gidiyor" — live but no numeric eta yet


async def stop_status(stop_no: str) -> list[dict] | None:
    """Every bus/line EGO returns for a stop number, live entries first.

    Returns None when the stop number itself was not recognised, the browser
    sidecar is unconfigured, or the lookup otherwise failed — callers degrade
    to an "unavailable/not found" message rather than raising, same
    convention as every other keyless lookup in this codebase (see
    services/aircraft.py).
    """
    stop_no = stop_no.strip()
    if not stop_no or not stop_no.isdigit():
        return None
    if not browser_service.configured():
        logger.warning("ego_bus_browser_unconfigured")
        return None
    try:
        result = await browser_service.act([
            {"action": "goto", "target": _FORM_URL},
            {"action": "fill", "target": _INPUT_SELECTOR, "value": stop_no},
            {"action": "click", "target": _SUBMIT_SELECTOR},
        ])
    except browser_service.BrowserUnavailable as exc:
        logger.warning("ego_bus_lookup_failed", extra={"stop": stop_no, "error": str(exc)})
        return None

    entries = _parse_text(result.get("text") or "")
    if not entries:
        return None
    entries.sort(key=_eta_key)
    return entries
