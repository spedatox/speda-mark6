"""
Google Workspace via the STANDARD REST APIs — not the gated preview MCP endpoints.

Google's remote MCP servers (gmailmcp.googleapis.com, …) require enrolment in the
Google Workspace Developer Preview Program and blanket-deny otherwise ("caller
does not have permission"), even with a valid token and every API enabled. The
standard REST APIs (gmail.googleapis.com, calendar, drive, people) work with the
exact same OAuth token, so SPEDA talks to those directly.

GoogleRestClient duck-types the MCPClient surface the registry already drives
(server_name / connect / list_tools / call_tool / disconnect), so registration,
lazy toolset loading, the Connections panel and the "Sign in with Google" flow
all keep working unchanged — only the transport underneath is REST instead of
the dead MCP endpoints. Tokens refresh on demand (cached), so a session no longer
dies after the ~1h access-token lifetime.
"""

import base64
import logging
import time
from email.message import EmailMessage
from typing import Awaitable, Callable

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_GMAIL = "https://gmail.googleapis.com/gmail/v1"
_CAL = "https://www.googleapis.com/calendar/v3"
_DRIVE = "https://www.googleapis.com/drive/v3"
_PEOPLE = "https://people.googleapis.com/v1"


# ── Shared access-token cache ─────────────────────────────────────────────────

class _Token:
    """One cached access token shared across every Google REST client. Refreshed
    from the stored refresh token + OAuth client when it's within 60s of expiry."""

    _access: str | None = None
    _exp: float = 0.0

    @classmethod
    async def get(cls) -> str | None:
        from app.core.runtime_state import get_google_refresh_token

        now = time.time()
        if cls._access and now < cls._exp - 60:
            return cls._access

        rt = get_google_refresh_token()
        if not (rt and settings.google_client_id and settings.google_client_secret):
            return None
        try:
            async with httpx.AsyncClient(timeout=15.0) as c:
                r = await c.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "client_id": settings.google_client_id,
                        "client_secret": settings.google_client_secret,
                        "refresh_token": rt,
                        "grant_type": "refresh_token",
                    },
                )
            if r.status_code != 200:
                logger.error(
                    "google_token_refresh_failed",
                    extra={"status": r.status_code, "body": r.text[:200]},
                )
                return None
            tok = r.json()
        except Exception as e:  # noqa: BLE001
            logger.error("google_token_refresh_error", extra={"error": str(e)})
            return None

        cls._access = tok.get("access_token")
        cls._exp = now + int(tok.get("expires_in", 3600))
        return cls._access


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

async def _calendar_dispatch(name: str, a: dict, token: str) -> str:
    if name == "calendar_list_events":
        from datetime import datetime, timezone
        params = {
            "maxResults": min(int(a.get("max_results", 10)), 25),
            "singleEvents": "true",
            "orderBy": "startTime",
            "timeMin": a.get("time_min") or datetime.now(timezone.utc).isoformat(),
        }
        if a.get("time_max"):
            params["timeMax"] = a["time_max"]
        if a.get("query"):
            params["q"] = a["query"]
        r = await _req("GET", f"{_CAL}/calendars/primary/events", token, params=params)
        r.raise_for_status()
        items = r.json().get("items", [])
        if not items:
            return "No events found in that range."
        out = []
        for ev in items:
            start = ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date", "?")
            loc = f" @ {ev['location']}" if ev.get("location") else ""
            out.append(f"- [{ev.get('id','')}] {start} — {ev.get('summary','(no title)')}{loc}")
        return f"{len(out)} event(s):\n" + "\n".join(out)

    if name == "calendar_create_event":
        body = {
            "summary": a["summary"],
            "start": {"dateTime": a["start"]},
            "end": {"dateTime": a["end"]},
        }
        if a.get("description"):
            body["description"] = a["description"]
        if a.get("location"):
            body["location"] = a["location"]
        r = await _req("POST", f"{_CAL}/calendars/primary/events", token, json=body)
        r.raise_for_status()
        ev = r.json()
        return f"Event '{a['summary']}' created: {ev.get('htmlLink', ev.get('id',''))}"

    return f"Unknown Calendar tool: {name}"


_CALENDAR_TOOLS = [
    {
        "name": "calendar_list_events",
        "description": (
            "Lists upcoming events from the owner's primary Google Calendar, optionally "
            "filtered by a free-text query or a time window. Use this to answer 'what's "
            "on my calendar', 'am I free Thursday', or to find an event before editing. "
            "Do NOT use it to create events (use calendar_create_event). Defaults to "
            "events from now onward; returns one line per event with its id."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "time_min": {"type": "string", "description": "RFC3339 lower bound (default now), e.g. '2026-06-15T00:00:00Z'."},
                "time_max": {"type": "string", "description": "RFC3339 upper bound (optional)."},
                "query": {"type": "string", "description": "Free-text filter on event text (optional)."},
                "max_results": {"type": "integer", "description": "Max events (default 10, cap 25)."},
            },
            "required": [],
        },
    },
    {
        "name": "calendar_create_event",
        "description": (
            "Creates an event on the owner's primary Google Calendar. Use this when the "
            "owner asks to schedule/add something and has given a title and start/end "
            "time. Do NOT use it to list or check availability (use calendar_list_events). "
            "Times are RFC3339 with offset. Returns the created event's link."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Event title."},
                "start": {"type": "string", "description": "Start, RFC3339 with offset, e.g. '2026-06-15T14:00:00+03:00'."},
                "end": {"type": "string", "description": "End, RFC3339 with offset."},
                "description": {"type": "string", "description": "Optional details."},
                "location": {"type": "string", "description": "Optional location."},
            },
            "required": ["summary", "start", "end"],
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


def build_google_clients(access_token: str | None = None) -> list[GoogleRestClient]:
    """Build the Google Workspace REST clients. `access_token` is accepted for
    signature-compatibility with the old MCP builder but ignored — clients fetch
    and cache their own token on demand via _Token."""
    return [
        GoogleRestClient("google_gmail", _GMAIL_TOOLS, _gmail_dispatch),
        GoogleRestClient("google_calendar", _CALENDAR_TOOLS, _calendar_dispatch),
        GoogleRestClient("google_drive", _DRIVE_TOOLS, _drive_dispatch),
        GoogleRestClient("google_people", _PEOPLE_TOOLS, _people_dispatch),
    ]
