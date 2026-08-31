# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Google Workspace via the STANDARD REST APIs — not the gated preview MCP endpoints.

Google's remote MCP servers (gmailmcp.googleapis.com, …) require enrolment in the
Google Workspace Developer Preview Program and blanket-deny otherwise ("caller
does not have permission"), even with a valid token and every API enabled. The
standard REST APIs (gmail.googleapis.com, calendar, drive, people) work with the
exact same OAuth token, so Speda talks to those directly.

GoogleRestClient duck-types the MCPClient surface the registry already drives
(server_name / connect / list_tools / call_tool / disconnect), so registration,
lazy toolset loading, the Connections panel and the "Sign in with Google" flow
all keep working unchanged — only the transport underneath is REST instead of
the dead MCP endpoints. Tokens refresh on demand (cached), so a session no longer
dies after the ~1h access-token lifetime.
"""

import base64
import logging
import re
import urllib.parse
from email.message import EmailMessage
from typing import Awaitable, Callable

import httpx

from app.config import settings
from app.services.google_auth import GoogleToken

logger = logging.getLogger(__name__)

_GMAIL = "https://gmail.googleapis.com/gmail/v1"
_CAL = "https://www.googleapis.com/calendar/v3"
_DRIVE = "https://www.googleapis.com/drive/v3"
_PEOPLE = "https://people.googleapis.com/v1"
_TASKS = "https://tasks.googleapis.com/tasks/v1"


# ── Shared access-token cache ─────────────────────────────────────────────────
# Lives in services/google_auth.py now: a SERVICE (the Octavius Protocol's Drive
# upload) needs a token too, and services may not import from `mcp/` — that is
# the wrong way down the layering. Aliased here so every call site below, and
# routers/connections.py clearing the cache on disconnect, stay unchanged.
_Token = GoogleToken


async def _req(method: str, url: str, token: str, **kwargs) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}", **kwargs.pop("headers", {})}
    async with httpx.AsyncClient(timeout=30.0) as c:
        return await c.request(method, url, headers=headers, **kwargs)


# ── Client that quacks like MCPClient but routes to REST ──────────────────────

Dispatch = Callable[[str, dict, str], Awaitable[str]]


class GoogleRestClient:
    def __init__(self, server_name: str, tools: list[dict], dispatch: Dispatch) -> None:
        self.server_name = server_name
        self._tools = tools
        self._dispatch = dispatch
        self._connected = False

    async def connect(self) -> None:
        # Registration reflects real connectivity: we can only "connect" if a
        # valid token can be obtained (refresh token present + redeemable).
        token = await _Token.get()
        if token is None:
            raise RuntimeError("Google not connected (no valid OAuth token)")
        self._connected = True

    async def list_tools(self) -> list[dict]:
        return self._tools

    async def call_tool(self, name: str, args: dict) -> str:
        token = await _Token.get()
        if not token:
            return ("Google isn't connected. Ask the owner to sign in via "
                    "Settings → Google Workspace, then try again.")
        try:
            return await self._dispatch(name, args or {}, token)
        except httpx.HTTPStatusError as e:
            return f"Google API error {e.response.status_code}: {e.response.text[:400]}"
        except Exception as e:  # noqa: BLE001
            logger.error("google_rest_call_failed", extra={"tool": name, "error": str(e)})
            return f"Google call failed: {e}"

    async def disconnect(self) -> None:
        self._connected = False


# ── Helpers ───────────────────────────────────────────────────────────────────

def _hdr(headers: list[dict], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _decode_part(data: str) -> str:
    try:
        return base64.urlsafe_b64decode(data + "===").decode("utf-8", "replace")
    except Exception:
        return ""


def _extract_body(payload: dict) -> str:
    """Walk a Gmail message payload for the best text body (plain > html)."""
    if not payload:
        return ""
    mime = payload.get("mimeType", "")
    body = payload.get("body", {})
    if mime == "text/plain" and body.get("data"):
        return _decode_part(body["data"])
    plain = html = ""
    for part in payload.get("parts", []) or []:
        got = _extract_body(part)
        if part.get("mimeType") == "text/plain" and got:
            plain = plain or got
        elif part.get("mimeType") == "text/html" and got:
            html = html or got
        elif got:
            plain = plain or got
    if plain:
        return plain
    if mime == "text/html" and body.get("data"):
        return _decode_part(body["data"])
    return html


# ── Gmail ──────────────────────────────────────────────────────────────────────

async def _gmail_dispatch(name: str, a: dict, token: str) -> str:
    if name == "gmail_search":
        q = a.get("query", "")
        n = min(int(a.get("max_results", 10)), 25)
        r = await _req("GET", f"{_GMAIL}/users/me/messages", token,
                       params={"q": q, "maxResults": n})
        r.raise_for_status()
        ids = [m["id"] for m in r.json().get("messages", [])]
        if not ids:
            return f"No messages match: {q!r}"
        out = []
        for mid in ids:
            mr = await _req("GET", f"{_GMAIL}/users/me/messages/{mid}", token,
                            params={"format": "metadata",
                                    "metadataHeaders": ["From", "Subject", "Date"]})
            if mr.status_code != 200:
                continue
            m = mr.json()
            h = m.get("payload", {}).get("headers", [])
            out.append(
                f"- [{mid}] {_hdr(h,'Date')[:25]} | {_hdr(h,'From')[:45]} | "
                f"{_hdr(h,'Subject')[:70] or '(no subject)'}\n    {m.get('snippet','')[:160]}"
            )
        return f"{len(out)} message(s) for {q!r}:\n" + "\n".join(out)

    if name == "gmail_read":
        mid = a["message_id"]
        r = await _req("GET", f"{_GMAIL}/users/me/messages/{mid}", token,
                       params={"format": "full"})
        r.raise_for_status()
        m = r.json()
        h = m.get("payload", {}).get("headers", [])
        body = _extract_body(m.get("payload", {}))
        return (f"From: {_hdr(h,'From')}\nTo: {_hdr(h,'To')}\nDate: {_hdr(h,'Date')}\n"
                f"Subject: {_hdr(h,'Subject')}\n\n{body[:6000]}")

    if name == "gmail_send":
        msg = EmailMessage()
        msg["To"] = a["to"]
        msg["Subject"] = a.get("subject", "")
        msg.set_content(a.get("body", ""))
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        r = await _req("POST", f"{_GMAIL}/users/me/messages/send", token, json={"raw": raw})
        r.raise_for_status()
        return f"Email sent to {a['to']} (id {r.json().get('id','?')})."

    return f"Unknown Gmail tool: {name}"


_GMAIL_TOOLS = [
    {
        "name": "gmail_search",
        "description": (
            "Searches the owner's Gmail using Gmail's native query syntax and returns "
            "a list of matching messages (id, sender, subject, date, snippet). Use this "
            "to find emails — e.g. 'is:unread', 'from:bank newer_than:7d', "
            "'subject:invoice has:attachment'. Do NOT use it to read a full message body "
            "(use gmail_read with an id) or to send (use gmail_send). Returns one line "
            "per message with the id needed for gmail_read."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Gmail search query, e.g. 'in:inbox is:unread newer_than:2d'."},
                "max_results": {"type": "integer", "description": "Max messages to return (default 10, cap 25)."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "gmail_read",
        "description": (
            "Reads one full Gmail message by its id — returns the From/To/Subject/Date "
            "headers and the decoded text body. Use this after gmail_search to open a "
            "specific email the owner asked about. Do NOT use it to list or search "
            "(use gmail_search). Returns the message header block followed by the body."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"message_id": {"type": "string", "description": "The Gmail message id from gmail_search."}},
            "required": ["message_id"],
        },
    },
    {
        "name": "gmail_send",
        "description": (
            "Sends an email from the owner's Gmail account to a recipient. Use this only "
            "when the owner explicitly asks to send/reply to an email and has approved "
            "the recipient, subject and body. Do NOT use it to draft silently or to "
            "search/read. Returns confirmation with the sent message id."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address."},
                "subject": {"type": "string", "description": "Subject line."},
                "body": {"type": "string", "description": "Plain-text email body."},
            },
            "required": ["to", "subject", "body"],
        },
    },
]


# ── Calendar ───────────────────────────────────────────────────────────────────
#
# Recurrence is the reason this section is long. Google models a repeating event
# as ONE parent carrying an RRULE plus generated instances, and the three things a
# human means by "change my Tuesday class" map onto three different API calls:
#
#   this instance      PATCH the INSTANCE id (from events/{id}/instances)
#   the whole series   PATCH the PARENT id (the one holding `recurrence`)
#   this and following split the series — trim the parent's RRULE with an UNTIL
#                      just before this occurrence, then create a fresh recurring
#                      event for the remainder (Google's own documented recipe;
#                      there is no single call that does it)
#
# Getting this wrong is not a cosmetic failure: patching the parent when the owner
# meant one occurrence silently rewrites every past and future instance. So scope
# is an explicit, required-by-description argument rather than something inferred.

_OFFSET = re.compile(r"[+-]\d{2}:\d{2}$")


def _time_field(value: str, tz: str) -> dict:
    """Turn a time string into Calendar's start/end object.

    Accepts a bare date (all-day event), an RFC3339 stamp with an offset, or a
    local stamp without one. The last case is the one that matters: a model
    writing '2026-06-15T14:00:00' means the OWNER's 2 p.m., and sending it with no
    zone makes Google reject it. Attaching the owner's IANA zone is what makes the
    natural thing the model writes also the correct thing.
    """
    value = (value or "").strip()
    if len(value) == 10 and value.count("-") == 2:
        return {"date": value}
    if value.endswith("Z") or _OFFSET.search(value):
        return {"dateTime": value}
    return {"dateTime": value, "timeZone": tz}


def _owner_tz() -> str:
    return settings.owner_timezone or "UTC"


def _normalize_rrule(rule: str) -> str:
    """Accept 'FREQ=WEEKLY;BYDAY=MO' or a full 'RRULE:FREQ=…' line, emit the
    latter — models produce both and Calendar only takes the prefixed form."""
    rule = (rule or "").strip()
    if not rule:
        return ""
    upper = rule.upper()
    if upper.startswith(("RRULE:", "RDATE:", "EXDATE:", "EXRULE:")):
        return rule
    return f"RRULE:{rule}"


def _event_line(ev: dict) -> str:
    start = ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date", "?")
    loc = f" @ {ev['location']}" if ev.get("location") else ""
    marks = []
    if ev.get("recurrence"):
        marks.append("repeats")
    if ev.get("recurringEventId"):
        marks.append(f"instance of {ev['recurringEventId']}")
    if (ev.get("status") or "") == "cancelled":
        marks.append("cancelled")
    tail = f"  ({', '.join(marks)})" if marks else ""
    return f"- [{ev.get('id','')}] {start} — {ev.get('summary','(no title)')}{loc}{tail}"


def _event_body(a: dict, tz: str, *, partial: bool) -> dict:
    """Assemble the request body shared by create and update. `partial` drives
    PATCH semantics: only keys the caller actually supplied are sent, so an
    update that changes the time doesn't erase the description."""
    body: dict = {}
    if a.get("summary") is not None or not partial:
        body["summary"] = a.get("summary", "")
    if a.get("start"):
        body["start"] = _time_field(a["start"], tz)
    if a.get("end"):
        body["end"] = _time_field(a["end"], tz)
    for key in ("description", "location"):
        if a.get(key) is not None:
            body[key] = a[key]
    if a.get("attendees") is not None:
        body["attendees"] = [{"email": e} for e in (a["attendees"] or [])]
    if a.get("recurrence") is not None:
        rules = a["recurrence"]
        rules = [rules] if isinstance(rules, str) else list(rules)
        body["recurrence"] = [_normalize_rrule(r) for r in rules if r] or None
        if body["recurrence"] is None:
            # Explicitly emptying the list is how a series becomes a one-off.
            body["recurrence"] = []
    if a.get("reminders_minutes") is not None:
        body["reminders"] = {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": int(m)}
                for m in (a["reminders_minutes"] or [])
            ],
        }
    return body


def _until_stamp(instance_start: dict) -> str:
    """UTC 'YYYYMMDDTHHMMSSZ' one second before an instance starts — the UNTIL
    value that trims a series to stop just short of it."""
    from datetime import datetime, timedelta, timezone as _tz

    raw = instance_start.get("dateTime") or instance_start.get("date") or ""
    if len(raw) == 10:
        moment = datetime.fromisoformat(raw).replace(tzinfo=_tz.utc)
    else:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=_tz.utc)
    return (moment.astimezone(_tz.utc) - timedelta(seconds=1)).strftime("%Y%m%dT%H%M%SZ")


def _apply_until(recurrence: list[str], until: str) -> list[str]:
    """Rewrite a recurrence list so its RRULE ends at `until`. COUNT and any
    existing UNTIL are dropped — both would fight the new bound."""
    out = []
    for rule in recurrence or []:
        if not rule.upper().startswith("RRULE:"):
            out.append(rule)
            continue
        parts = [
            p for p in rule[len("RRULE:"):].split(";")
            if p and not p.upper().startswith(("UNTIL=", "COUNT="))
        ]
        parts.append(f"UNTIL={until}")
        out.append("RRULE:" + ";".join(parts))
    return out


async def _get_event(token: str, cal: str, event_id: str) -> dict:
    r = await _req("GET", f"{_CAL}/calendars/{cal}/events/{event_id}", token)
    r.raise_for_status()
    return r.json()


async def _split_series(token: str, cal: str, instance: dict) -> tuple[dict, str]:
    """Trim the parent series so it stops before `instance`. Returns the parent
    as it was BEFORE trimming (so the caller can clone the tail) and the UNTIL
    stamp used."""
    parent_id = instance.get("recurringEventId")
    if not parent_id:
        raise ValueError("that event is not an instance of a recurring series")
    parent = await _get_event(token, cal, parent_id)
    start = instance.get("originalStartTime") or instance.get("start") or {}
    until = _until_stamp(start)
    trimmed = _apply_until(parent.get("recurrence") or [], until)
    if not trimmed:
        raise ValueError("the parent event carries no recurrence rule to trim")
    r = await _req(
        "PATCH", f"{_CAL}/calendars/{cal}/events/{parent_id}", token,
        json={"recurrence": trimmed},
    )
    r.raise_for_status()
    return parent, until


async def _calendar_dispatch(name: str, a: dict, token: str) -> str:
    cal = urllib.parse.quote(a.get("calendar_id") or "primary", safe="")
    tz = _owner_tz()

    if name == "calendar_list_calendars":
        r = await _req("GET", f"{_CAL}/users/me/calendarList", token,
                       params={"maxResults": 50, "showHidden": "false"})
        r.raise_for_status()
        items = r.json().get("items", [])
        if not items:
            return "No calendars found on this account."
        return f"{len(items)} calendar(s):\n" + "\n".join(
            f"- [{c.get('id','')}] {c.get('summary','(untitled)')}"
            + (" (primary)" if c.get("primary") else "")
            + f" · {c.get('accessRole','')}"
            for c in items
        )

    if name == "calendar_list_events":
        from datetime import datetime, timezone
        params = {
            "maxResults": min(int(a.get("max_results", 10)), 50),
            # Expand the series into occurrences — otherwise a weekly class shows
            # once, at its first-ever date, and "what's on Thursday" is wrong.
            "singleEvents": "true",
            "orderBy": "startTime",
            "timeMin": a.get("time_min") or datetime.now(timezone.utc).isoformat(),
        }
        if a.get("time_max"):
            params["timeMax"] = a["time_max"]
        if a.get("query"):
            params["q"] = a["query"]
        r = await _req("GET", f"{_CAL}/calendars/{cal}/events", token, params=params)
        r.raise_for_status()
        items = r.json().get("items", [])
        if not items:
            return "No events found in that range."
        return f"{len(items)} event(s):\n" + "\n".join(_event_line(ev) for ev in items)

    if name == "calendar_get_event":
        ev = await _get_event(token, cal, a["event_id"])
        lines = [
            f"{ev.get('summary','(no title)')}  [{ev.get('id','')}]",
            f"Start: {ev.get('start',{}).get('dateTime') or ev.get('start',{}).get('date','?')}",
            f"End:   {ev.get('end',{}).get('dateTime') or ev.get('end',{}).get('date','?')}",
        ]
        if ev.get("location"):
            lines.append(f"Where: {ev['location']}")
        if ev.get("recurrence"):
            lines.append(f"Repeats: {'; '.join(ev['recurrence'])}")
        if ev.get("recurringEventId"):
            lines.append(f"Instance of series: {ev['recurringEventId']}")
        if ev.get("attendees"):
            lines.append("Attendees: " + ", ".join(
                f"{p.get('email','')}({p.get('responseStatus','?')})" for p in ev["attendees"]
            ))
        if ev.get("description"):
            lines.append(f"\n{ev['description'][:2000]}")
        if ev.get("htmlLink"):
            lines.append(f"\n{ev['htmlLink']}")
        return "\n".join(lines)

    if name == "calendar_list_instances":
        params = {"maxResults": min(int(a.get("max_results", 10)), 50)}
        if a.get("time_min"):
            params["timeMin"] = a["time_min"]
        if a.get("time_max"):
            params["timeMax"] = a["time_max"]
        r = await _req("GET", f"{_CAL}/calendars/{cal}/events/{a['event_id']}/instances",
                       token, params=params)
        r.raise_for_status()
        items = r.json().get("items", [])
        if not items:
            return "That series has no instances in the requested range."
        return (f"{len(items)} occurrence(s) of {a['event_id']} — use one of THESE ids to "
                f"change a single occurrence:\n" + "\n".join(_event_line(ev) for ev in items))

    if name == "calendar_create_event":
        body = _event_body({**a, "summary": a["summary"]}, tz, partial=False)
        params = {}
        if a.get("attendees"):
            params["sendUpdates"] = "all"
        r = await _req("POST", f"{_CAL}/calendars/{cal}/events", token,
                       json=body, params=params)
        r.raise_for_status()
        ev = r.json()
        repeats = f" Repeats: {'; '.join(ev.get('recurrence', []))}." if ev.get("recurrence") else ""
        return (f"Event '{ev.get('summary','')}' created [{ev.get('id','')}].{repeats} "
                f"{ev.get('htmlLink','')}")

    if name == "calendar_update_event":
        scope = (a.get("scope") or "single").strip().lower()
        event_id = a["event_id"]
        changes = _event_body(a, tz, partial=True)
        if not changes:
            return "Nothing to update — supply at least one field to change."

        if scope == "following":
            instance = await _get_event(token, cal, event_id)
            try:
                parent, _ = await _split_series(token, cal, instance)
            except ValueError as e:
                return f"Cannot split that series: {e}"
            # Clone the parent's shape, apply the changes, and start the new
            # series at this occurrence. The old half keeps its own history.
            tail = {
                k: v for k, v in parent.items()
                if k in ("summary", "description", "location", "attendees",
                         "recurrence", "reminders", "colorId", "transparency")
            }
            tail["start"] = changes.get("start") or instance.get("start")
            tail["end"] = changes.get("end") or instance.get("end")
            tail.update({k: v for k, v in changes.items() if k not in ("start", "end")})
            created = await _req("POST", f"{_CAL}/calendars/{cal}/events", token, json=tail)
            created.raise_for_status()
            new = created.json()
            return (f"Series split: occurrences before {instance.get('start',{}).get('dateTime','?')} "
                    f"keep the old settings, and this one onward became a new series "
                    f"[{new.get('id','')}]. {new.get('htmlLink','')}")

        if scope == "all":
            # Resolve the instance to its parent — the model usually has an
            # instance id in hand, and patching that would change only one day.
            current = await _get_event(token, cal, event_id)
            event_id = current.get("recurringEventId") or event_id

        params = {"sendUpdates": "all"} if a.get("attendees") is not None else {}
        r = await _req("PATCH", f"{_CAL}/calendars/{cal}/events/{event_id}", token,
                       json=changes, params=params)
        r.raise_for_status()
        ev = r.json()
        what = {"all": "the whole series", "single": "that occurrence"}.get(scope, scope)
        return f"Updated {what}: '{ev.get('summary','')}' [{ev.get('id','')}]. {ev.get('htmlLink','')}"

    if name == "calendar_delete_event":
        scope = (a.get("scope") or "single").strip().lower()
        event_id = a["event_id"]

        if scope == "following":
            instance = await _get_event(token, cal, event_id)
            try:
                _, until = await _split_series(token, cal, instance)
            except ValueError as e:
                return f"Cannot trim that series: {e}"
            return (f"Series trimmed — it now ends at {until}. This occurrence and every "
                    f"later one are gone; earlier ones are untouched.")

        if scope == "all":
            current = await _get_event(token, cal, event_id)
            event_id = current.get("recurringEventId") or event_id

        r = await _req("DELETE", f"{_CAL}/calendars/{cal}/events/{event_id}", token,
                       params={"sendUpdates": "all"})
        if r.status_code not in (200, 204, 410):
            r.raise_for_status()
        what = "the whole series" if scope == "all" else "that occurrence"
        return f"Deleted {what} ({event_id})."

    if name == "calendar_move_event":
        r = await _req("POST", f"{_CAL}/calendars/{cal}/events/{a['event_id']}/move",
                       token, params={"destination": a["destination_calendar_id"]})
        r.raise_for_status()
        return f"Event moved to calendar {a['destination_calendar_id']}."

    if name == "calendar_respond_to_event":
        # RSVP = patch the owner's own attendee row. Everyone else's response is
        # theirs and must survive the write.
        ev = await _get_event(token, cal, a["event_id"])
        me = await _req("GET", f"{_CAL}/calendars/{cal}", token)
        my_email = me.json().get("id", "") if me.status_code == 200 else ""
        attendees = ev.get("attendees") or []
        hit = False
        for person in attendees:
            if person.get("self") or person.get("email", "").lower() == my_email.lower():
                person["responseStatus"] = a["response"]
                hit = True
        if not hit:
            return ("You are not listed as an attendee on that event, so there is "
                    "nothing to RSVP to.")
        r = await _req("PATCH", f"{_CAL}/calendars/{cal}/events/{a['event_id']}", token,
                       json={"attendees": attendees}, params={"sendUpdates": "all"})
        r.raise_for_status()
        return f"RSVP set to '{a['response']}' on {ev.get('summary','the event')}."

    if name == "calendar_freebusy":
        ids = a.get("calendar_ids") or [a.get("calendar_id") or "primary"]
        r = await _req("POST", f"{_CAL}/freeBusy", token, json={
            "timeMin": a["time_min"],
            "timeMax": a["time_max"],
            "timeZone": tz,
            "items": [{"id": i} for i in ids],
        })
        r.raise_for_status()
        cals = r.json().get("calendars", {})
        out = []
        for cal_id, data in cals.items():
            busy = data.get("busy", [])
            if not busy:
                out.append(f"- {cal_id}: completely free in that window.")
                continue
            out.append(f"- {cal_id}: busy " + ", ".join(
                f"{b['start']}→{b['end']}" for b in busy
            ))
        return "Free/busy:\n" + "\n".join(out)

    return f"Unknown Calendar tool: {name}"


_SCOPE_PROP = {
    "type": "string",
    "enum": ["single", "all", "following"],
    "description": (
        "Which occurrences this affects, for a REPEATING event. 'single' (default) "
        "= only the one occurrence whose instance id you passed — get instance ids "
        "from calendar_list_instances. 'all' = the entire series, past and future; "
        "you may pass either an instance id or the series id. 'following' = this "
        "occurrence and every later one, leaving earlier ones untouched (this "
        "splits the series). Irrelevant for one-off events — leave it unset. When "
        "the owner has not said which they mean, ASK; picking 'all' by mistake "
        "silently rewrites months of history."
    ),
}

_CALENDAR_ID_PROP = {
    "type": "string",
    "description": "Calendar id from calendar_list_calendars. Default 'primary'.",
}

_CALENDAR_TOOLS = [
    {
        "name": "calendar_list_calendars",
        "description": (
            "Lists every Google Calendar on the owner's account — personal, work, "
            "university timetable, shared and subscribed — with each one's id and access "
            "level. Use this first when the owner mentions a calendar other than their "
            "default one, or when an event needs to land somewhere specific, because "
            "every other calendar tool takes an id from here. Do NOT use it to read "
            "events (that is calendar_list_events). Returns one line per calendar."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "calendar_list_events",
        "description": (
            "Lists events from one of the owner's Google Calendars, optionally filtered "
            "by a free-text query or a time window. Repeating events are expanded into "
            "their individual occurrences, so this is what answers 'what's on Thursday', "
            "'what's my week like' or 'when is my next lecture'. Use it to find an event "
            "before editing or deleting it — the id in each line is what the other tools "
            "take. Do NOT use it to check whether a slot is free across several calendars "
            "(that is calendar_freebusy). Returns one line per occurrence with its id."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "calendar_id": _CALENDAR_ID_PROP,
                "time_min": {"type": "string", "description": "RFC3339 lower bound (default now), e.g. '2026-06-15T00:00:00Z'."},
                "time_max": {"type": "string", "description": "RFC3339 upper bound (optional)."},
                "query": {"type": "string", "description": "Free-text filter on event text (optional)."},
                "max_results": {"type": "integer", "description": "Max events (default 10, cap 50)."},
            },
            "required": [],
        },
    },
    {
        "name": "calendar_get_event",
        "description": (
            "Reads one calendar event in full by id — times, location, attendees and "
            "their RSVP states, the recurrence rule if it repeats, and the description "
            "body. Use it when the owner asks for the details of something "
            "calendar_list_events only summarised, or to check what a series' repeat rule "
            "actually says before changing it. Do NOT use it to browse (that is "
            "calendar_list_events). Returns the event's full detail block."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "Event id from calendar_list_events."},
                "calendar_id": _CALENDAR_ID_PROP,
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "calendar_list_instances",
        "description": (
            "Lists the individual occurrences of ONE repeating event, each with its own "
            "instance id. This is the tool that makes 'move just next Tuesday's class' "
            "possible: changing a single occurrence requires that occurrence's instance "
            "id, which is different from the series id. Use it whenever the owner wants "
            "to change or cancel one date of something that repeats. Do NOT use it on a "
            "one-off event. Returns one line per occurrence."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "The recurring event's series id."},
                "calendar_id": _CALENDAR_ID_PROP,
                "time_min": {"type": "string", "description": "RFC3339 lower bound (optional)."},
                "time_max": {"type": "string", "description": "RFC3339 upper bound (optional)."},
                "max_results": {"type": "integer", "description": "Max occurrences (default 10, cap 50)."},
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "calendar_create_event",
        "description": (
            "Creates an event on one of the owner's Google Calendars, optionally "
            "REPEATING, with attendees and custom reminders. Use it when the owner asks "
            "to schedule something and has given at least a title and a start time — for "
            "a weekly class or a monthly payment, pass `recurrence` and create ONE "
            "repeating event rather than many copies. Do NOT use it to change something "
            "that already exists (that is calendar_update_event) or to check availability "
            "first (calendar_list_events / calendar_freebusy). Times without a UTC offset "
            "are read in the owner's own timezone. Returns the new event's id and link."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Event title."},
                "start": {"type": "string", "description": "Start: '2026-06-15T14:00:00' (owner's timezone), RFC3339 with offset, or 'YYYY-MM-DD' for all-day."},
                "end": {"type": "string", "description": "End, same formats. All-day ends are EXCLUSIVE (a one-day event ends the next date)."},
                "calendar_id": _CALENDAR_ID_PROP,
                "description": {"type": "string", "description": "Optional details."},
                "location": {"type": "string", "description": "Optional location."},
                "recurrence": {
                    "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
                    "description": (
                        "Optional RFC 5545 repeat rule(s). Examples: 'FREQ=WEEKLY;BYDAY=TU' "
                        "(every Tuesday), 'FREQ=WEEKLY;BYDAY=MO,WE;UNTIL=20270115T000000Z' "
                        "(Mondays and Wednesdays until January), 'FREQ=MONTHLY;BYMONTHDAY=1', "
                        "'FREQ=DAILY;COUNT=10'. The 'RRULE:' prefix is added if you omit it."
                    ),
                },
                "attendees": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Optional attendee email addresses. Supplying any sends invitations.",
                },
                "reminders_minutes": {
                    "type": "array", "items": {"type": "integer"},
                    "description": "Optional popup reminders, in minutes before the start (e.g. [10, 60]).",
                },
            },
            "required": ["summary", "start", "end"],
        },
    },
    {
        "name": "calendar_update_event",
        "description": (
            "Changes an existing calendar event — its time, title, location, description, "
            "attendees, reminders or repeat rule. Only the fields you pass are touched, so "
            "moving an event does not wipe its notes. For a REPEATING event the `scope` "
            "argument decides whether you are changing one occurrence, the whole series, "
            "or this occurrence and all later ones; read that argument's description "
            "before calling, because the wrong choice rewrites dates the owner never "
            "mentioned. Do NOT use it to create (calendar_create_event) or to cancel "
            "(calendar_delete_event). Returns what was changed and the event link."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "Event or instance id."},
                "calendar_id": _CALENDAR_ID_PROP,
                "scope": _SCOPE_PROP,
                "summary": {"type": "string", "description": "New title."},
                "start": {"type": "string", "description": "New start. Bare local times are read in the owner's timezone."},
                "end": {"type": "string", "description": "New end."},
                "description": {"type": "string", "description": "New details."},
                "location": {"type": "string", "description": "New location."},
                "recurrence": {
                    "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
                    "description": "New repeat rule(s), e.g. 'FREQ=WEEKLY;BYDAY=TH'. Pass an empty array to stop it repeating.",
                },
                "attendees": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Replacement attendee list (this REPLACES the existing one and re-sends invites).",
                },
                "reminders_minutes": {
                    "type": "array", "items": {"type": "integer"},
                    "description": "Replacement popup reminders, in minutes before the start.",
                },
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "calendar_delete_event",
        "description": (
            "Deletes a calendar event, or one occurrence of a repeating one. As with "
            "updating, `scope` decides how much disappears: one occurrence, the entire "
            "series, or this occurrence onward. Use it when the owner asks to cancel or "
            "remove something and has confirmed WHICH — a cancelled class next week and a "
            "cancelled class forever are different instructions and this tool cannot tell "
            "them apart on its own. Do NOT use it to decline an invitation you were sent "
            "(that is calendar_respond_to_event, which tells the organiser). Returns "
            "confirmation of what was removed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "Event or instance id."},
                "calendar_id": _CALENDAR_ID_PROP,
                "scope": _SCOPE_PROP,
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "calendar_move_event",
        "description": (
            "Moves an event from one of the owner's calendars to another, keeping its id, "
            "guests and details. Use it when something was filed in the wrong place — a "
            "personal appointment that landed on the university calendar, say. Do NOT use "
            "it to change an event's TIME (that is calendar_update_event; 'move' here "
            "means between calendars, not to another hour). Returns confirmation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "Event id to move."},
                "calendar_id": {"type": "string", "description": "Source calendar id (default 'primary')."},
                "destination_calendar_id": {"type": "string", "description": "Target calendar id from calendar_list_calendars."},
            },
            "required": ["event_id", "destination_calendar_id"],
        },
    },
    {
        "name": "calendar_respond_to_event",
        "description": (
            "RSVPs to an event the owner was invited to — accepted, declined or "
            "tentative — which notifies the organiser, unlike deleting it. Use it when "
            "the owner says they are or are not going to something someone else "
            "scheduled. Do NOT use it on events the owner created themselves (they have "
            "no RSVP to give) and never RSVP without being told to. Returns the response "
            "that was recorded."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "Event id from calendar_list_events."},
                "calendar_id": _CALENDAR_ID_PROP,
                "response": {
                    "type": "string",
                    "enum": ["accepted", "declined", "tentative"],
                    "description": "The RSVP to send.",
                },
            },
            "required": ["event_id", "response"],
        },
    },
    {
        "name": "calendar_freebusy",
        "description": (
            "Reports the busy blocks across one or more of the owner's calendars in a time "
            "window, without listing what the events actually are. Use it to answer 'am I "
            "free Thursday afternoon' or to find a slot before proposing a meeting time — "
            "it is far cheaper and clearer than listing every event and reading titles you "
            "do not need. Do NOT use it when the owner wants to know WHAT is scheduled "
            "(that is calendar_list_events). Returns the busy intervals per calendar."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "time_min": {"type": "string", "description": "RFC3339 start of the window."},
                "time_max": {"type": "string", "description": "RFC3339 end of the window."},
                "calendar_ids": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Calendars to check (default: just the primary one).",
                },
            },
            "required": ["time_min", "time_max"],
        },
    },
]


# ── Tasks ──────────────────────────────────────────────────────────────────────
# Google Tasks is a separate API and a separate scope from Calendar, and it is
# where a to-do without a fixed hour belongs. Keeping it distinct matters: an
# errand parked on the calendar at an invented 09:00 is a lie about the owner's
# day, and it is exactly what happens when the only writable surface is Calendar.

def _task_line(t: dict) -> str:
    done = "✓" if t.get("status") == "completed" else "☐"
    due = f" · due {t['due'][:10]}" if t.get("due") else ""
    indent = "    " if t.get("parent") else ""
    notes = f"\n{indent}      {t['notes'][:120]}" if t.get("notes") else ""
    return f"{indent}- {done} [{t.get('id','')}] {t.get('title','(untitled)')}{due}{notes}"


def _due_stamp(value: str) -> str:
    """Google Tasks stores `due` as an RFC3339 timestamp but only ever honours
    the DATE part — a time of day is accepted and then ignored. Normalising here
    stops a model's '2026-06-15T14:00:00' from silently losing its afternoon and
    looking like the API misbehaved."""
    value = (value or "").strip()
    if not value:
        return ""
    return f"{value[:10]}T00:00:00.000Z"


async def _tasks_dispatch(name: str, a: dict, token: str) -> str:
    if name == "tasks_list_lists":
        r = await _req("GET", f"{_TASKS}/users/@me/lists", token, params={"maxResults": 100})
        r.raise_for_status()
        items = r.json().get("items", [])
        if not items:
            return "No task lists on this account."
        return f"{len(items)} task list(s):\n" + "\n".join(
            f"- [{l.get('id','')}] {l.get('title','(untitled)')}" for l in items
        )

    if name == "tasks_create_list":
        r = await _req("POST", f"{_TASKS}/users/@me/lists", token, json={"title": a["title"]})
        r.raise_for_status()
        created = r.json()
        return f"Task list '{created.get('title','')}' created [{created.get('id','')}]."

    tasklist = a.get("tasklist_id") or "@default"

    if name == "tasks_list":
        params = {
            "maxResults": min(int(a.get("max_results", 20) or 20), 100),
            "showCompleted": "true" if a.get("show_completed") else "false",
            "showHidden": "true" if a.get("show_completed") else "false",
        }
        if a.get("due_min"):
            params["dueMin"] = _due_stamp(a["due_min"])
        if a.get("due_max"):
            params["dueMax"] = _due_stamp(a["due_max"])
        r = await _req("GET", f"{_TASKS}/lists/{tasklist}/tasks", token, params=params)
        r.raise_for_status()
        items = r.json().get("items", [])
        if not items:
            return "No tasks in that list matching those filters."
        # Parents before their subtasks, so the indentation means something.
        roots = [t for t in items if not t.get("parent")]
        children: dict[str, list[dict]] = {}
        for t in items:
            if t.get("parent"):
                children.setdefault(t["parent"], []).append(t)
        lines = []
        for t in roots:
            lines.append(_task_line(t))
            lines.extend(_task_line(c) for c in children.get(t.get("id", ""), []))
        orphans = [t for t in items if t.get("parent") and t["parent"] not in
                   {r.get("id") for r in roots}]
        lines.extend(_task_line(t) for t in orphans)
        return f"{len(items)} task(s):\n" + "\n".join(lines)

    if name == "tasks_create":
        body = {"title": a["title"]}
        if a.get("notes"):
            body["notes"] = a["notes"]
        if a.get("due"):
            body["due"] = _due_stamp(a["due"])
        params = {}
        if a.get("parent_task_id"):
            params["parent"] = a["parent_task_id"]
        r = await _req("POST", f"{_TASKS}/lists/{tasklist}/tasks", token,
                       json=body, params=params)
        r.raise_for_status()
        t = r.json()
        due = f" (due {t['due'][:10]})" if t.get("due") else ""
        return f"Task '{t.get('title','')}' added{due} [{t.get('id','')}]."

    if name == "tasks_update":
        body: dict = {}
        if a.get("title") is not None:
            body["title"] = a["title"]
        if a.get("notes") is not None:
            body["notes"] = a["notes"]
        if a.get("due") is not None:
            # An empty string clears the due date; Tasks wants null, not "".
            body["due"] = _due_stamp(a["due"]) or None
        if a.get("completed") is not None:
            # `status` alone is the switch — Tasks clears or sets the `completed`
            # timestamp itself. Writing that field directly is rejected.
            body["status"] = "completed" if a["completed"] else "needsAction"
        if not body:
            return "Nothing to update — supply a title, notes, due date or completed flag."
        r = await _req("PATCH", f"{_TASKS}/lists/{tasklist}/tasks/{a['task_id']}", token,
                       json=body)
        r.raise_for_status()
        t = r.json()
        state = "completed" if t.get("status") == "completed" else "open"
        return f"Task '{t.get('title','')}' updated ({state})."

    if name == "tasks_delete":
        r = await _req("DELETE", f"{_TASKS}/lists/{tasklist}/tasks/{a['task_id']}", token)
        if r.status_code not in (200, 204):
            r.raise_for_status()
        return f"Task {a['task_id']} deleted."

    if name == "tasks_move":
        params = {}
        if a.get("parent_task_id"):
            params["parent"] = a["parent_task_id"]
        if a.get("previous_task_id"):
            params["previous"] = a["previous_task_id"]
        r = await _req("POST", f"{_TASKS}/lists/{tasklist}/tasks/{a['task_id']}/move",
                       token, params=params)
        r.raise_for_status()
        return f"Task {a['task_id']} repositioned."

    if name == "tasks_clear_completed":
        r = await _req("POST", f"{_TASKS}/lists/{tasklist}/clear", token)
        if r.status_code not in (200, 204):
            r.raise_for_status()
        return "Completed tasks cleared from that list (they are hidden, not lost)."

    return f"Unknown Tasks tool: {name}"


_TASKLIST_PROP = {
    "type": "string",
    "description": "Task list id from tasks_list_lists. Default '@default' (the owner's main list).",
}

_TASKS_TOOLS = [
    {
        "name": "tasks_list_lists",
        "description": (
            "Lists the owner's Google Tasks lists with their ids — the separate to-do "
            "lists they keep, e.g. a default one plus 'University' or 'Shopping'. Use it "
            "when the owner names a list, or before adding something that clearly belongs "
            "somewhere other than the default. Do NOT use it to read the tasks themselves "
            "(that is tasks_list). Returns one line per list."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "tasks_create_list",
        "description": (
            "Creates a new Google Tasks list. Use it only when the owner explicitly asks "
            "for a new list to keep something separate — do NOT create one on your own "
            "initiative just because a task doesn't seem to fit an existing list, since "
            "scattered lists are how a to-do system stops being read. Returns the new "
            "list's id."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"title": {"type": "string", "description": "Name for the new list."}},
            "required": ["title"],
        },
    },
    {
        "name": "tasks_list",
        "description": (
            "Reads the owner's Google Tasks — their actual to-do list — optionally "
            "narrowed to a due-date range, with subtasks shown under their parents. Use it "
            "to answer 'what do I have to do', 'what's due this week', or to find a task's "
            "id before completing or editing it. Completed tasks are hidden unless you ask "
            "for them. Do NOT use it for scheduled events with a fixed hour — those live "
            "on the calendar (calendar_list_events). Returns one line per task with its id "
            "and due date."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tasklist_id": _TASKLIST_PROP,
                "show_completed": {"type": "boolean", "description": "Include finished tasks (default false)."},
                "due_min": {"type": "string", "description": "Only tasks due on/after this date (YYYY-MM-DD)."},
                "due_max": {"type": "string", "description": "Only tasks due on/before this date (YYYY-MM-DD)."},
                "max_results": {"type": "integer", "description": "Max tasks (default 20, cap 100)."},
            },
            "required": [],
        },
    },
    {
        "name": "tasks_create",
        "description": (
            "Adds a to-do to the owner's Google Tasks, optionally with notes, a due date "
            "and a parent task to make it a subtask. Use this — not the calendar — for "
            "anything that must be DONE but has no fixed hour: 'submit the form by Friday', "
            "'renew the transport card'. Putting an errand on the calendar at an invented "
            "time misrepresents the owner's day. Do NOT use it for meetings, lectures or "
            "anything with a start and end time (calendar_create_event). Note that Google "
            "Tasks honours only the DATE of a due date, never the time. Returns the new "
            "task's id."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "What needs doing."},
                "tasklist_id": _TASKLIST_PROP,
                "notes": {"type": "string", "description": "Optional detail or context."},
                "due": {"type": "string", "description": "Optional due date, YYYY-MM-DD. Times are ignored by Google Tasks."},
                "parent_task_id": {"type": "string", "description": "Optional: make this a subtask of that task."},
            },
            "required": ["title"],
        },
    },
    {
        "name": "tasks_update",
        "description": (
            "Changes a task, and is how a task gets marked DONE — pass completed=true. Can "
            "also retitle it, edit its notes, move its due date, or reopen a finished task "
            "with completed=false. Only the fields you pass change. Use it when the owner "
            "says they've finished something or wants a deadline moved. Do NOT use it to "
            "remove a task entirely (that is tasks_delete — completing and deleting mean "
            "different things and a completed task stays in the record). Returns the "
            "task's new state."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task id from tasks_list."},
                "tasklist_id": _TASKLIST_PROP,
                "title": {"type": "string", "description": "New title."},
                "notes": {"type": "string", "description": "New notes."},
                "due": {"type": "string", "description": "New due date YYYY-MM-DD, or an empty string to clear it."},
                "completed": {"type": "boolean", "description": "True marks it done; false reopens it."},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "tasks_delete",
        "description": (
            "Permanently removes a task from the owner's Google Tasks. Use it only when "
            "the owner says a task should never have been there or is no longer relevant "
            "at all. Do NOT use it when they say they FINISHED something — that is "
            "tasks_update with completed=true, which keeps the record of it having been "
            "done. Returns confirmation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task id from tasks_list."},
                "tasklist_id": _TASKLIST_PROP,
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "tasks_move",
        "description": (
            "Repositions a task within its list — makes it a subtask of another task, or "
            "moves it to sit after a specific one. Use it when the owner wants their list "
            "reordered or a task nested under a bigger piece of work. Do NOT use it to "
            "change a due date or title (tasks_update) or to move between lists, which "
            "Google Tasks does not support. Returns confirmation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task to move."},
                "tasklist_id": _TASKLIST_PROP,
                "parent_task_id": {"type": "string", "description": "Become a subtask of this task. Omit to move to the top level."},
                "previous_task_id": {"type": "string", "description": "Place it directly after this task. Omit to place it first."},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "tasks_clear_completed",
        "description": (
            "Hides every completed task in a list, the way the Tasks app's 'clear "
            "completed' button does — they leave the list but are not destroyed. Use it "
            "when the owner asks to tidy up a list clogged with finished items. Do NOT use "
            "it as a way to delete tasks (tasks_delete) and do NOT run it unasked; a "
            "finished list is often exactly what someone wants to look at. Returns "
            "confirmation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"tasklist_id": _TASKLIST_PROP},
            "required": [],
        },
    },
]


# ── Drive ──────────────────────────────────────────────────────────────────────

async def _drive_dispatch(name: str, a: dict, token: str) -> str:
    if name == "drive_search":
        n = min(int(a.get("max_results", 10)), 25)
        q = a.get("query", "")
        # Treat a bare term as a full-text search; pass raw Drive query syntax through.
        drive_q = q if (":" in q or "=" in q or "contains" in q) else f"name contains '{q}' or fullText contains '{q}'"
        r = await _req("GET", f"{_DRIVE}/files", token, params={
            "q": f"({drive_q}) and trashed = false",
            "pageSize": n,
            "fields": "files(id,name,mimeType,modifiedTime,webViewLink)",
            "orderBy": "modifiedTime desc",
        })
        r.raise_for_status()
        files = r.json().get("files", [])
        if not files:
            return f"No Drive files match: {q!r}"
        out = [f"- [{f['id']}] {f.get('name','?')} ({f.get('mimeType','').split('.')[-1]}) "
               f"{f.get('modifiedTime','')[:10]}" for f in files]
        return f"{len(out)} file(s) for {q!r}:\n" + "\n".join(out)

    if name == "drive_read":
        fid = a["file_id"]
        meta = await _req("GET", f"{_DRIVE}/files/{fid}", token, params={"fields": "name,mimeType"})
        meta.raise_for_status()
        mime = meta.json().get("mimeType", "")
        if mime.startswith("application/vnd.google-apps."):
            # Google-native doc → export as plain text
            export = "text/plain"
            r = await _req("GET", f"{_DRIVE}/files/{fid}/export", token, params={"mimeType": export})
        else:
            r = await _req("GET", f"{_DRIVE}/files/{fid}", token, params={"alt": "media"})
        r.raise_for_status()
        return f"{meta.json().get('name','file')}:\n\n{r.text[:6000]}"

    return f"Unknown Drive tool: {name}"


_DRIVE_TOOLS = [
    {
        "name": "drive_search",
        "description": (
            "Searches the owner's Google Drive by name and full-text content and returns "
            "matching files (id, name, type, modified date). Use this to locate a "
            "document, sheet or PDF the owner mentions. You can pass raw Drive query "
            "syntax (e.g. \"mimeType='application/pdf'\") or a plain term. Do NOT use it "
            "to read a file's contents (use drive_read with an id). Returns one line per file."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search term or raw Drive query."},
                "max_results": {"type": "integer", "description": "Max files (default 10, cap 25)."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "drive_read",
        "description": (
            "Reads the text content of one Google Drive file by id — Google Docs are "
            "exported to plain text, other text files are downloaded directly. Use this "
            "after drive_search to open a document the owner asked about. Do NOT use it "
            "on binary files like images or large videos. Returns the file name and its "
            "text (truncated)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"file_id": {"type": "string", "description": "Drive file id from drive_search."}},
            "required": ["file_id"],
        },
    },
]


# ── Contacts (People) ──────────────────────────────────────────────────────────

async def _people_dispatch(name: str, a: dict, token: str) -> str:
    if name == "contacts_search":
        r = await _req("GET", f"{_PEOPLE}/people:searchContacts", token, params={
            "query": a.get("query", ""),
            "readMask": "names,emailAddresses,phoneNumbers",
            "pageSize": min(int(a.get("max_results", 10)), 25),
        })
        r.raise_for_status()
        results = r.json().get("results", [])
        if not results:
            return f"No contacts match: {a.get('query','')!r}"
        out = []
        for res in results:
            p = res.get("person", {})
            nm = (p.get("names") or [{}])[0].get("displayName", "(no name)")
            em = ", ".join(e.get("value", "") for e in (p.get("emailAddresses") or []))
            ph = ", ".join(t.get("value", "") for t in (p.get("phoneNumbers") or []))
            out.append(f"- {nm}" + (f" | {em}" if em else "") + (f" | {ph}" if ph else ""))
        return f"{len(out)} contact(s):\n" + "\n".join(out)

    return f"Unknown Contacts tool: {name}"


_PEOPLE_TOOLS = [
    {
        "name": "contacts_search",
        "description": (
            "Searches the owner's Google Contacts by name, email or phone and returns "
            "matches with their names, emails and phone numbers. Use this to look up "
            "someone's email before drafting a message, or to find a phone number. Do "
            "NOT use it for org-directory-wide lookups beyond saved contacts. Returns "
            "one line per contact."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Name, email or phone fragment to search for."},
                "max_results": {"type": "integer", "description": "Max contacts (default 10, cap 25)."},
            },
            "required": ["query"],
        },
    },
]


# ── Public surface for non-LLM callers ─────────────────────────────────────────
# services/mail_watch.py is polled by n8n and must never go through the tool /
# orchestrator layer (that would spend a turn per poll), but it needs exactly the
# token cache, request path and MIME walking the tools use. These aliases are
# that seam: one Gmail implementation, two callers. Import these, not the
# underscored originals.

GMAIL_API = _GMAIL
google_access_token = _Token.get
google_api_request = _req
gmail_header = _hdr
gmail_text_body = _extract_body


def build_google_clients(access_token: str | None = None) -> list[GoogleRestClient]:
    """Build the Google Workspace REST clients. `access_token` is accepted for
    signature-compatibility with the old MCP builder but ignored — clients fetch
    and cache their own token on demand via _Token."""
    return [
        GoogleRestClient("google_gmail", _GMAIL_TOOLS, _gmail_dispatch),
        GoogleRestClient("google_calendar", _CALENDAR_TOOLS, _calendar_dispatch),
        GoogleRestClient("google_tasks", _TASKS_TOOLS, _tasks_dispatch),
        GoogleRestClient("google_drive", _DRIVE_TOOLS, _drive_dispatch),
        GoogleRestClient("google_people", _PEOPLE_TOOLS, _people_dispatch),
    ]
