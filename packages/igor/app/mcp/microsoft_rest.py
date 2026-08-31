# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Microsoft 365 (Outlook) via the Microsoft Graph REST API.

The owner's university account (@ostimteknik.edu.tr) is a Microsoft work/school
mailbox, so Gmail's path does not reach it — this is the second mail estate, not
a second way into the first. The shape deliberately mirrors app/mcp/google_rest.py
one-for-one: a cached token refreshed from a stored refresh token, a client that
duck-types the MCPClient surface the registry already drives, and a set of public
aliases at the bottom for the non-LLM probe in services/outlook_watch.py.

Why the same shape rather than a shared base: the two providers agree on almost
nothing below the seam (Graph returns JSON bodies with HTML content and OData
paging where Gmail returns base64 MIME parts and a two-step list/get), and the
one thing they DO share — "is this really from that domain" — is already shared,
imported from services/mail_watch.py rather than reimplemented.

Auth is the OAuth 2.0 authorization-code flow against
login.microsoftonline.com/{tenant}, with `offline_access` in the scope set —
without it Microsoft issues no refresh token at all and the connection dies an
hour after sign-in. Access tokens last ~1h and are refreshed on demand here.
Tenant defaults to `common` so a school account and a personal account can both
sign in through the same app registration.
"""

import html
import logging
import re
import time
from typing import Awaitable, Callable

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"

# The delegated scopes Speda asks for. Mail.ReadWrite rather than Mail.Read
# because the watch marks handled mail with a category, and a category is a
# write. offline_access is what makes the connection outlive the first hour.
MS_SCOPES = [
    "offline_access",
    "openid",
    "profile",
    "User.Read",
    "Mail.ReadWrite",
    "Mail.Send",
    "Calendars.Read",
]


def _authority() -> str:
    tenant = (settings.microsoft_tenant or "common").strip() or "common"
    return f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0"


def authorize_url(state: str = "") -> str:
    """Consent URL for the 'Connect Microsoft' button."""
    import urllib.parse

    params = {
        "client_id": settings.microsoft_client_id,
        "response_type": "code",
        "redirect_uri": settings.microsoft_oauth_redirect,
        "response_mode": "query",
        "scope": " ".join(MS_SCOPES),
        # Force the consent screen so a re-connect after a scope change actually
        # re-consents instead of silently reissuing the old, narrower grant.
        "prompt": "consent",
    }
    if state:
        params["state"] = state
    return f"{_authority()}/authorize?" + urllib.parse.urlencode(params)


async def exchange_code(code: str) -> dict:
    """Authorization code → token response. Raises httpx errors to the caller."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{_authority()}/token",
            data={
                "client_id": settings.microsoft_client_id,
                "client_secret": settings.microsoft_client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": settings.microsoft_oauth_redirect,
                "scope": " ".join(MS_SCOPES),
            },
        )
        resp.raise_for_status()
        return resp.json()


# ── Shared access-token cache ─────────────────────────────────────────────────

class _Token:
    """One cached access token shared across every Graph client. Refreshed from
    the stored refresh token when it's within 60s of expiry.

    Microsoft rotates the refresh token on most refreshes, so the new one is
    written back — dropping it would strand the connection at the point the old
    token expires (90 days of inactivity, or sooner under a CA policy).
    """

    _access: str | None = None
    _exp: float = 0.0

    @classmethod
    async def get(cls) -> str | None:
        from app.core.runtime_state import (
            get_microsoft_refresh_token,
            set_microsoft_refresh_token,
        )

        now = time.time()
        if cls._access and now < cls._exp - 60:
            return cls._access

        rt = get_microsoft_refresh_token()
        if not (rt and settings.microsoft_client_id and settings.microsoft_client_secret):
            return None
        try:
            async with httpx.AsyncClient(timeout=15.0) as c:
                r = await c.post(
                    f"{_authority()}/token",
                    data={
                        "client_id": settings.microsoft_client_id,
                        "client_secret": settings.microsoft_client_secret,
                        "refresh_token": rt,
                        "grant_type": "refresh_token",
                        "scope": " ".join(MS_SCOPES),
                    },
                )
            if r.status_code != 200:
                logger.error(
                    "microsoft_token_refresh_failed",
                    extra={"status": r.status_code, "body": r.text[:200]},
                )
                return None
            tok = r.json()
        except Exception as e:  # noqa: BLE001
            logger.error("microsoft_token_refresh_error", extra={"error": str(e)})
            return None

        cls._access = tok.get("access_token")
        cls._exp = now + int(tok.get("expires_in", 3600))
        rotated = tok.get("refresh_token")
        if rotated and rotated != rt:
            set_microsoft_refresh_token(rotated)
        return cls._access

    @classmethod
    def clear(cls) -> None:
        cls._access = None
        cls._exp = 0.0


async def _req(method: str, url: str, token: str, **kwargs) -> httpx.Response:
    headers = {
        "Authorization": f"Bearer {token}",
        # Graph returns HTML bodies by default; text costs far fewer tokens and
        # is what a model actually wants to read.
        "Prefer": 'outlook.body-content-type="text"',
        **kwargs.pop("headers", {}),
    }
    async with httpx.AsyncClient(timeout=30.0) as c:
        return await c.request(method, url, headers=headers, **kwargs)


# ── Client that quacks like MCPClient but routes to Graph ─────────────────────

Dispatch = Callable[[str, dict, str], Awaitable[str]]


class MicrosoftRestClient:
    def __init__(self, server_name: str, tools: list[dict], dispatch: Dispatch) -> None:
        self.server_name = server_name
        self._tools = tools
        self._dispatch = dispatch
        self._connected = False

    async def connect(self) -> None:
        token = await _Token.get()
        if token is None:
            raise RuntimeError("Microsoft not connected (no valid OAuth token)")
        self._connected = True

    async def list_tools(self) -> list[dict]:
        return self._tools

    async def call_tool(self, name: str, args: dict) -> str:
        token = await _Token.get()
        if not token:
            return ("Microsoft 365 isn't connected. Ask the owner to sign in via "
                    "Settings → Connections → Microsoft 365, then try again.")
        try:
            return await self._dispatch(name, args or {}, token)
        except httpx.HTTPStatusError as e:
            return f"Microsoft Graph error {e.response.status_code}: {e.response.text[:400]}"
        except Exception as e:  # noqa: BLE001
            logger.error("microsoft_rest_call_failed", extra={"tool": name, "error": str(e)})
            return f"Microsoft Graph call failed: {e}"

    async def disconnect(self) -> None:
        self._connected = False


# ── Helpers ───────────────────────────────────────────────────────────────────

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t]*\n[ \t]*")


def message_text(message: dict) -> str:
    """Best-effort plain text for a Graph message.

    The `Prefer: outlook.body-content-type="text"` header usually gets us text
    already, but Graph honours it inconsistently across mailbox types, so an
    HTML body still has to be survivable rather than dumped raw into a prompt.
    """
    body = message.get("body") or {}
    content = body.get("content") or message.get("bodyPreview") or ""
    if (body.get("contentType") or "").lower() == "html" or "<" in content[:200]:
        content = re.sub(r"(?is)<(script|style).*?</\1>", " ", content)
        content = re.sub(r"(?i)<br\s*/?>|</p>", "\n", content)
        content = _TAG.sub(" ", content)
        content = html.unescape(content)
    content = _WS.sub("\n", content)
    return re.sub(r"\n{3,}", "\n\n", content).strip()


def sender_address(message: dict) -> str:
    """The bare From address of a Graph message, lowercased."""
    holder = message.get("from") or message.get("sender") or {}
    return ((holder.get("emailAddress") or {}).get("address") or "").lower()


def _recipients(message: dict, field: str = "toRecipients") -> str:
    out = []
    for r in message.get(field) or []:
        addr = (r.get("emailAddress") or {})
        name, mail = addr.get("name", ""), addr.get("address", "")
        out.append(f"{name} <{mail}>" if name and name != mail else mail)
    return ", ".join(out)


def _summary_line(m: dict) -> str:
    who = (m.get("from") or {}).get("emailAddress") or {}
    flag = "●" if not m.get("isRead", True) else " "
    attach = " 📎" if m.get("hasAttachments") else ""
    return (
        f"- {flag} [{m.get('id','')}] {(m.get('receivedDateTime') or '')[:16]} | "
        f"{(who.get('name') or who.get('address') or '?')[:38]} | "
        f"{(m.get('subject') or '(no subject)')[:70]}{attach}\n"
        f"      {(m.get('bodyPreview') or '')[:150]}"
    )


_LIST_SELECT = (
    "id,conversationId,subject,from,toRecipients,receivedDateTime,"
    "bodyPreview,isRead,hasAttachments,webLink,categories"
)


# ── Outlook mail ──────────────────────────────────────────────────────────────

async def _outlook_dispatch(name: str, a: dict, token: str) -> str:
    if name == "outlook_search":
        top = min(int(a.get("max_results", 10) or 10), 25)
        query = (a.get("query") or "").strip()
        folder = (a.get("folder") or "").strip()
        base = f"{GRAPH}/me/mailFolders/{folder}/messages" if folder else f"{GRAPH}/me/messages"
        params: dict = {"$select": _LIST_SELECT, "$top": top}
        if query:
            # KQL search. Graph forbids combining $search with $orderby, and
            # relevance ordering is the right default for a search anyway. The
            # value is wrapped in quotes, so an embedded quote would terminate
            # the expression early and produce a 400 rather than a search.
            params["$search"] = '"{}"'.format(query.replace('"', " ").strip())
        else:
            params["$orderby"] = "receivedDateTime desc"
        if a.get("unread_only"):
            # $filter and $search cannot be combined on messages, so an unread
            # search is filtered after the fact rather than refused.
            if query:
                params["$top"] = min(top * 3, 50)
            else:
                params["$filter"] = "isRead eq false"
        r = await _req("GET", base, token, params=params,
                       headers={"ConsistencyLevel": "eventual"} if query else {})
        r.raise_for_status()
        items = r.json().get("value", [])
        if a.get("unread_only") and query:
            items = [m for m in items if not m.get("isRead", True)][:top]
        if not items:
            return f"No Outlook messages match: {query or '(inbox)'}"
        return (f"{len(items)} message(s){' for ' + repr(query) if query else ''}:\n"
                + "\n".join(_summary_line(m) for m in items))

    if name == "outlook_read":
        mid = a["message_id"]
        r = await _req("GET", f"{GRAPH}/me/messages/{mid}", token,
                       params={"$select": "id,subject,from,toRecipients,ccRecipients,"
                                          "receivedDateTime,body,hasAttachments,webLink"})
        r.raise_for_status()
        m = r.json()
        who = (m.get("from") or {}).get("emailAddress") or {}
        header = (
            f"From: {who.get('name','')} <{who.get('address','')}>\n"
            f"To: {_recipients(m)}\n"
        )
        if m.get("ccRecipients"):
            header += f"Cc: {_recipients(m, 'ccRecipients')}\n"
        header += (
            f"Date: {m.get('receivedDateTime','')}\n"
            f"Subject: {m.get('subject','')}\n"
        )
        if m.get("hasAttachments"):
            att = await _req("GET", f"{GRAPH}/me/messages/{mid}/attachments", token,
                             params={"$select": "id,name,size,contentType"})
            if att.status_code == 200:
                names = [f"{x.get('name','?')} ({x.get('size',0)}B)"
                         for x in att.json().get("value", [])]
                if names:
                    header += f"Attachments: {', '.join(names)}\n"
        return header + "\n" + message_text(m)[:6000]

    if name == "outlook_send":
        to = a["to"] if isinstance(a["to"], list) else [a["to"]]
        body = {
            "message": {
                "subject": a.get("subject", ""),
                "body": {"contentType": "Text", "content": a.get("body", "")},
                "toRecipients": [{"emailAddress": {"address": addr}} for addr in to],
            },
            "saveToSentItems": True,
        }
        if a.get("cc"):
            cc = a["cc"] if isinstance(a["cc"], list) else [a["cc"]]
            body["message"]["ccRecipients"] = [
                {"emailAddress": {"address": addr}} for addr in cc
            ]
        r = await _req("POST", f"{GRAPH}/me/sendMail", token, json=body)
        r.raise_for_status()
        return f"Email sent from the Microsoft account to {', '.join(to)}."

    if name == "outlook_reply":
        mid = a["message_id"]
        endpoint = "replyAll" if a.get("reply_all") else "reply"
        r = await _req("POST", f"{GRAPH}/me/messages/{mid}/{endpoint}", token,
                       json={"comment": a.get("body", "")})
        r.raise_for_status()
        return f"Reply sent on message {mid}."

    if name == "outlook_list_folders":
        r = await _req("GET", f"{GRAPH}/me/mailFolders", token,
                       params={"$select": "id,displayName,unreadItemCount,totalItemCount",
                               "$top": 50})
        r.raise_for_status()
        folders = r.json().get("value", [])
        if not folders:
            return "No mail folders returned."
        return "Mail folders:\n" + "\n".join(
            f"- [{f.get('id','')[:24]}…] {f.get('displayName','?')} "
            f"({f.get('unreadItemCount',0)} unread / {f.get('totalItemCount',0)})"
            for f in folders
        )

    if name == "outlook_mark_read":
        mid = a["message_id"]
        r = await _req("PATCH", f"{GRAPH}/me/messages/{mid}", token,
                       json={"isRead": bool(a.get("read", True))})
        r.raise_for_status()
        return f"Message {mid} marked {'read' if a.get('read', True) else 'unread'}."

    return f"Unknown Outlook tool: {name}"


_OUTLOOK_TOOLS = [
    {
        "name": "outlook_search",
        "description": (
            "Searches the owner's Microsoft 365 / Outlook mailbox — the UNIVERSITY "
            "account, which is a completely separate mailbox from Gmail — and returns "
            "matching messages (id, sender, subject, date, preview, read state). Use it "
            "for anything about school, the university, lecturers, registrar or exam "
            "mail, or when the owner says 'my school mail' / 'okul maili'. Leave `query` "
            "empty to list the most recent messages in the inbox. Do NOT use it for "
            "personal Gmail (that is gmail_search) and do NOT use it to read a full "
            "message body (use outlook_read with an id). Returns one line per message "
            "with the id needed for outlook_read."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Free-text search over sender, subject and body (KQL). Examples: "
                        "'vize', 'from:ogrenciisleri@ostimteknik.edu.tr', 'transcript'. "
                        "Empty = most recent mail, newest first."
                    ),
                },
                "unread_only": {"type": "boolean", "description": "Only unread messages."},
                "folder": {
                    "type": "string",
                    "description": (
                        "Optional folder id or well-known name ('inbox', 'sentitems', "
                        "'drafts', 'archive'). Default: the whole mailbox."
                    ),
                },
                "max_results": {"type": "integer", "description": "Max messages (default 10, cap 25)."},
            },
            "required": [],
        },
    },
    {
        "name": "outlook_read",
        "description": (
            "Reads one full Microsoft 365 / Outlook message by its id — returns the "
            "From/To/Cc/Date/Subject headers, the attachment list, and the decoded plain-"
            "text body (HTML mail is stripped to text). Use this after outlook_search to "
            "open a specific university email the owner asked about, before summarising a "
            "deadline or drafting a reply. Do NOT use it to list or search (use "
            "outlook_search) and do NOT pass a Gmail message id — the two id spaces are "
            "unrelated. Returns the header block followed by the body text."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "The Outlook message id from outlook_search."},
            },
            "required": ["message_id"],
        },
    },
    {
        "name": "outlook_send",
        "description": (
            "Sends a new email FROM the owner's Microsoft 365 / university account, which "
            "is what makes it reach staff who only accept mail from the university domain. "
            "Use this only when the owner explicitly asks to send something from their "
            "school address and has approved the recipient, subject and body. Do NOT use "
            "it to reply in an existing thread (use outlook_reply, which keeps the "
            "threading) and do NOT send from here when the owner meant their personal "
            "address (that is gmail_send). Returns a confirmation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {
                    "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
                    "description": "Recipient address, or a list of addresses.",
                },
                "subject": {"type": "string", "description": "Subject line."},
                "body": {"type": "string", "description": "Plain-text email body."},
                "cc": {
                    "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
                    "description": "Optional Cc address or addresses.",
                },
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "outlook_reply",
        "description": (
            "Replies to an existing Outlook message in its own thread, quoting the "
            "original the way Outlook does. Use this when the owner approves a response "
            "to a specific university email — it preserves the conversation so the "
            "lecturer or office sees the thread rather than a fresh message. Do NOT use "
            "it to start a new conversation (use outlook_send) and never send without the "
            "owner's explicit approval of the text. Returns a confirmation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "Id of the message being replied to."},
                "body": {"type": "string", "description": "The reply text."},
                "reply_all": {"type": "boolean", "description": "Reply to everyone on the thread (default false)."},
            },
            "required": ["message_id", "body"],
        },
    },
    {
        "name": "outlook_list_folders",
        "description": (
            "Lists the mail folders in the owner's Microsoft 365 mailbox with their "
            "unread and total counts. Use it to find a folder id before a scoped "
            "outlook_search, or to answer 'how much unread do I have at school'. Do NOT "
            "use it to read messages (outlook_search / outlook_read do that). Returns one "
            "line per folder with its id."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "outlook_mark_read",
        "description": (
            "Marks one Outlook message as read (or back to unread). Use it after the "
            "owner has dealt with something and asks you to clear it, or to restore an "
            "unread flag you changed by mistake. Do NOT mark mail read on your own "
            "initiative — an unread badge is the owner's own to-do list, and clearing it "
            "silently hides work from them. Returns a confirmation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "The Outlook message id."},
                "read": {"type": "boolean", "description": "True = read (default), False = unread."},
            },
            "required": ["message_id"],
        },
    },
]


# ── Public surface for non-LLM callers ────────────────────────────────────────
# services/outlook_watch.py is polled by n8n and must never go through the tool /
# orchestrator layer (that would spend a turn per poll), but it needs exactly the
# token cache, request path and body decoding the tools use. Same seam as
# google_rest.py's aliases: one Graph implementation, two callers.

GRAPH_API = GRAPH
microsoft_access_token = _Token.get
microsoft_api_request = _req
LIST_SELECT = _LIST_SELECT


def build_microsoft_clients() -> list[MicrosoftRestClient]:
    """Build the Microsoft 365 REST clients. Tokens are fetched and cached on
    demand via _Token, so no access token has to be passed in."""
    return [
        MicrosoftRestClient("microsoft_outlook", _OUTLOOK_TOOLS, _outlook_dispatch),
    ]
