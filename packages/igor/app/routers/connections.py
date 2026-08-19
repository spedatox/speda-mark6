"""Connections — view/toggle MCP servers from the Settings UI.

Toggling a server hides/shows its tools live (no restart), which shrinks or
grows the cached prompt prefix — the lever for staying under the ITPM limit.
"""

import logging
import urllib.parse

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.config import settings
from app.mcp.servers import RESERVED_SERVER_NAMES

logger = logging.getLogger(__name__)
router = APIRouter(tags=["connections"])

# Scopes for the Google services Speda uses. One scope set per registered MCP
# service — a missing scope makes that service's tools return PERMISSION_DENIED
# ("caller is not authorized") at call time even though the MCP handshake and
# tool listing succeed. Chat had NO scope at all before, and People (Contacts)
# needs directory + profile, not just contacts.readonly.
# NOTE: changing this set invalidates the stored refresh token — the user must
# disconnect and re-run "Sign in with Google" to grant the new scopes, and the
# matching scopes must be present on the OAuth consent screen in Google Cloud.
_GOOGLE_SCOPES = [
    # Gmail
    "https://www.googleapis.com/auth/gmail.modify",
    # Calendar — full read/write, including recurrence edits and RSVPs
    "https://www.googleapis.com/auth/calendar",
    # Tasks — the owner's to-do list, a separate API and a separate scope from
    # Calendar. Without this every tasks_* tool returns PERMISSION_DENIED even
    # though the connection and the tool listing look perfectly healthy.
    "https://www.googleapis.com/auth/tasks",
    # Drive
    "https://www.googleapis.com/auth/drive",
    # Chat
    "https://www.googleapis.com/auth/chat.messages",
    "https://www.googleapis.com/auth/chat.spaces.readonly",
    # People / Contacts
    "https://www.googleapis.com/auth/contacts.readonly",
    "https://www.googleapis.com/auth/directory.readonly",
    "https://www.googleapis.com/auth/userinfo.profile",
]

# Human-friendly metadata for known servers (label + what credential it needs).
_INFO = {
    "tavily":           {"label": "Tavily — Web Search", "needs": "TAVILY_API_KEY"},
    "exa":              {"label": "Exa — Deep Search", "needs": "EXA_API_KEY"},
    "notion":           {"label": "Notion", "needs": None},
    "alpha_vantage":    {"label": "Alpha Vantage — Finance", "needs": "ALPHA_VANTAGE_API_KEY"},
    "github":           {"label": "GitHub", "needs": "GITHUB_TOKEN"},
    "brave_search":     {"label": "Brave Search", "needs": "BRAVE_SEARCH_API_KEY"},
    "fetch":            {"label": "Fetch — Read Pages", "needs": None},
    "filesystem":       {"label": "Filesystem", "needs": None},
    "arxiv":            {"label": "arXiv — Papers", "needs": None},
    "cve_intelligence": {"label": "CVE Intelligence", "needs": None},
    "google_gmail":     {"label": "Google — Gmail", "needs": "GOOGLE_*"},
    "google_calendar":  {"label": "Google — Calendar", "needs": "GOOGLE_*"},
    "google_tasks":     {"label": "Google — Tasks", "needs": "GOOGLE_*"},
    "google_drive":     {"label": "Google — Drive", "needs": "GOOGLE_*"},
    "google_chat":      {"label": "Google — Chat", "needs": "GOOGLE_*"},
    "google_people":    {"label": "Google — Contacts", "needs": "GOOGLE_*"},
    "microsoft_outlook": {"label": "Microsoft 365 — Outlook", "needs": "MICROSOFT_*"},
}


def _oauth_result_page(msg: str, ok: bool) -> str:
    """The tab the provider redirects back to. Every OAuth callback in this
    router renders the same one — three near-identical copies of it had already
    drifted apart in wording."""
    color = "#4fa377" if ok else "#c84a3a"
    return f"""<!doctype html><html><body style="background:#06121a;color:#cadbe2;
    font-family:system-ui;display:flex;align-items:center;justify-content:center;
    height:100vh;margin:0"><div style="text-align:center">
    <div style="font-size:2rem;color:{color}">{'✓' if ok else '✕'}</div>
    <h2>{msg}</h2><p style="color:#7a96a1">You can close this tab and return to Speda.</p>
    </div></body></html>"""


@router.get("/connections")
async def list_connections(request: Request):
    """Loaded MCP servers with status + a live prefix-budget estimate."""
    registry = request.app.state.registry
    rows = registry.server_summary()
    for r in rows:
        meta = _INFO.get(r["server"], {})
        r["label"] = meta.get("label", r["server"])
        r["needs"] = meta.get("needs")
    # With lazy loading, only always-on servers sit in the prefix by default;
    # the rest load on demand and don't count toward the baseline cold-write.
    baseline_tokens = sum(r["tokens"] for r in rows if r.get("always_on") and r["active"])
    return {
        "servers": rows,
        # Tier-1 Sonnet ITPM is 30k; the cached cold-write must fit under it.
        "active_tool_tokens": baseline_tokens,
        "itpm_limit": 30000,
        "lazy": True,
    }


@router.post("/connections")
async def toggle_connection(body: dict):
    """Body: {server, active}. Hides/shows the server's tools live."""
    from app.core.runtime_state import set_server_active
    server = body.get("server", "")
    active = bool(body.get("active", True))
    if not server:
        return {"error": "missing 'server'"}
    set_server_active(server, active)
    return {"server": server, "active": active}


# ── Owner-defined MCP servers ───────────────────────────────────────────────
# "In the nature of MCP you just enter the credentials and use it" — so the
# Settings panel can add one directly: a command for stdio, or a URL plus headers
# for streamable HTTP. The record is persisted, connected live (no restart), and
# re-registered at startup like any built-in. See app/core/runtime_state.py for
# the storage contract and app/mcp/servers.py for the client builder.

@router.get("/connections/mcp")
async def list_custom_mcp(request: Request):
    """Every hand-added server, with credentials masked and live status attached."""
    from app.core.runtime_state import get_custom_mcp_servers, mask_custom_mcp_server

    live = {row["server"]: row for row in request.app.state.registry.server_summary()}
    out = []
    for record in get_custom_mcp_servers():
        row = mask_custom_mcp_server(record)
        status = live.get(record.get("name", ""), {})
        row["connected"] = bool(status.get("connected"))
        row["tools"] = status.get("tools", 0)
        row["tokens"] = status.get("tokens", 0)
        out.append(row)
    return {"servers": out, "reserved": sorted(RESERVED_SERVER_NAMES)}


@router.post("/connections/mcp")
async def save_custom_mcp(request: Request, body: dict):
    """Add or update a server, then connect it immediately.

    Body: {name, transport: 'stdio'|'http', command: str|list, url, env: {},
           headers: {}, enabled: bool, note: str}

    The connection attempt is part of the save on purpose: a server that was
    stored but never reached is indistinguishable from one that works until the
    next time an agent needs it, which is the worst moment to find out.
    """
    import datetime
    import re as _re

    from app.core.runtime_state import (
        get_custom_mcp_server,
        mask_custom_mcp_server,
        save_custom_mcp_server,
    )

    name = (body.get("name") or "").strip()
    if not _re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,40}", name):
        return {"error": "Name must be 2–41 characters: lowercase letters, digits, "
                         "'_' or '-', starting with a letter or digit."}
    if name in RESERVED_SERVER_NAMES:
        return {"error": f"'{name}' is a built-in server name. Pick another — two "
                         f"servers sharing a name would silently hijack each other's tools."}

    transport = (body.get("transport") or "stdio").strip().lower()
    if transport not in ("stdio", "http"):
        return {"error": "transport must be 'stdio' or 'http'."}
    if transport == "http" and not (body.get("url") or "").strip():
        return {"error": "An http server needs a url (the MCP endpoint, e.g. "
                         "https://example.com/mcp)."}
    if transport == "stdio" and not body.get("command"):
        return {"error": "A stdio server needs a command, e.g. "
                         "'npx -y @scope/some-mcp-server'."}

    existing = get_custom_mcp_server(name)
    record = {
        "name": name,
        "transport": transport,
        "command": body.get("command") or [],
        "url": (body.get("url") or "").strip(),
        "env": dict(body.get("env") or {}),
        "headers": dict(body.get("headers") or {}),
        "enabled": bool(body.get("enabled", True)),
        "note": (body.get("note") or "")[:200],
        "added_at": existing.get("added_at") or datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat(timespec="seconds"),
    }
    stored = save_custom_mcp_server(record)

    if not stored.get("enabled", True):
        return {"server": mask_custom_mcp_server(stored), "connected": False,
                "message": "Saved, but left switched off."}

    from app.mcp.servers import build_custom_client
    registry = request.app.state.registry
    try:
        connected = await registry.reconnect_mcp_servers([build_custom_client(stored)])
    except Exception as e:  # noqa: BLE001
        logger.error("custom_mcp_connect_failed", extra={"server": name, "error": str(e)})
        return {"server": mask_custom_mcp_server(stored), "connected": False,
                "error": f"Saved, but the connection failed: {e}"}

    summary = next(
        (r for r in registry.server_summary() if r["server"] == name), {"tools": 0}
    )
    if not connected:
        return {
            "server": mask_custom_mcp_server(stored), "connected": False,
            "error": "Saved, but the server did not answer the MCP handshake. Check "
                     "the command/URL and any credentials, then save again.",
        }
    logger.info("custom_mcp_connected", extra={"server": name, "tools": summary.get("tools", 0)})
    return {
        "server": mask_custom_mcp_server(stored),
        "connected": True,
        "tools": summary.get("tools", 0),
    }


@router.delete("/connections/mcp/{name}")
async def remove_custom_mcp(request: Request, name: str):
    """Forget a hand-added server and drop its tools from the live registry."""
    from app.core.runtime_state import delete_custom_mcp_server

    existed = delete_custom_mcp_server(name)
    try:
        await request.app.state.registry.drop_mcp_server(name)
    except Exception as e:  # noqa: BLE001
        logger.warning("custom_mcp_drop_failed", extra={"server": name, "error": str(e)})
    return {"deleted": existed, "server": name}


# ── Google one-click sign-in ────────────────────────────────────────────────

@router.get("/connections/google/login")
async def google_login():
    """Return the Google consent URL for the 'Sign in with Google' button.
    Requires the app's OAuth client (GOOGLE_CLIENT_ID/SECRET) to be configured."""
    if not settings.google_client_id or not settings.google_client_secret:
        return {
            "error": "Google OAuth client not configured. Set GOOGLE_CLIENT_ID and "
                     "GOOGLE_CLIENT_SECRET in the backend .env (one-time, in Google "
                     "Cloud Console → Credentials → Desktop OAuth client).",
        }
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_oauth_redirect,
        "response_type": "code",
        "scope": " ".join(_GOOGLE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    }
    return {"auth_url": "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)}


@router.get("/connections/google/status")
async def google_status():
    """Whether Google is connected (a refresh token is stored). Drives the
    Settings UI so the 'Sign in' button is replaced by a connected state."""
    from app.core.runtime_state import get_google_refresh_token
    return {"connected": bool(get_google_refresh_token())}


@router.post("/connections/google/disconnect")
async def google_disconnect():
    """Forget the stored Google login. Tools will report 'not connected' until
    the owner signs in again."""
    from app.core.runtime_state import set_google_refresh_token
    from app.mcp.google_rest import _Token
    set_google_refresh_token("")
    _Token._access = None
    _Token._exp = 0.0
    logger.info("google_disconnected")
    return {"connected": False}


@router.get("/oauth/google/callback", response_class=HTMLResponse)
async def google_callback(request: Request, code: str = "", error: str = ""):
    """Google redirects here after consent. Exchange the code for a refresh
    token, persist it, and live-connect the Google MCP servers."""
    page = _oauth_result_page

    if error or not code:
        return HTMLResponse(page(f"Google sign-in failed: {error or 'no code'}", False), status_code=400)

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": settings.google_oauth_redirect,
                },
                timeout=15.0,
            )
            resp.raise_for_status()
            tok = resp.json()
    except Exception as e:  # noqa: BLE001
        logger.error("google_oauth_exchange_failed", extra={"error": str(e)})
        return HTMLResponse(page(f"Token exchange failed: {e}", False), status_code=400)

    refresh = tok.get("refresh_token")
    access = tok.get("access_token")
    if not refresh:
        return HTMLResponse(page("Google returned no refresh token (try again).", False), status_code=400)

    from app.core.runtime_state import set_google_refresh_token
    set_google_refresh_token(refresh)

    # Live-connect the Google servers so they work without a restart.
    try:
        from app.mcp.servers import build_google_clients
        registry = request.app.state.registry
        n = await registry.reconnect_mcp_servers(build_google_clients(access))
        logger.info("google_connected_via_ui", extra={"servers": n})
        return HTMLResponse(page(f"Google connected — {n} services live.", True))
    except Exception as e:  # noqa: BLE001
        logger.error("google_live_connect_failed", extra={"error": str(e)})
        # Token is saved; a restart will pick it up even if live connect failed.
        return HTMLResponse(page("Google signed in (restart to activate).", True))


# ── Microsoft 365 one-click sign-in ─────────────────────────────────────────
# The owner's university mailbox. Same three-endpoint shape as Google (login /
# status / disconnect + the redirect callback) so the Settings panel treats every
# account identically; the flow specifics live in app/mcp/microsoft_rest.py.

@router.get("/connections/microsoft/login")
async def microsoft_login():
    """Return the Microsoft consent URL for the 'Connect Microsoft 365' button."""
    if not settings.microsoft_client_id or not settings.microsoft_client_secret:
        return {
            "error": "Microsoft OAuth client not configured. Set MICROSOFT_CLIENT_ID "
                     "and MICROSOFT_CLIENT_SECRET in Settings → Configuration → "
                     "Microsoft 365 (one-time, in Azure Portal → App registrations → "
                     "your app → Certificates & secrets).",
        }
    from app.mcp.microsoft_rest import authorize_url
    return {"auth_url": authorize_url()}


@router.get("/connections/microsoft/status")
async def microsoft_status():
    """Whether Microsoft 365 is connected (a refresh token is stored)."""
    from app.core.runtime_state import get_microsoft_refresh_token
    return {"connected": bool(get_microsoft_refresh_token())}


@router.post("/connections/microsoft/disconnect")
async def microsoft_disconnect():
    """Forget the stored Microsoft login. Outlook tools will report 'not
    connected' until the owner signs in again."""
    from app.core.runtime_state import set_microsoft_refresh_token
    from app.mcp.microsoft_rest import _Token
    set_microsoft_refresh_token("")
    _Token.clear()
    logger.info("microsoft_disconnected")
    return {"connected": False}


@router.get("/oauth/microsoft/callback", response_class=HTMLResponse)
async def microsoft_callback(
    request: Request, code: str = "", error: str = "", error_description: str = ""
):
    """Microsoft redirects here after consent. Exchange the code for a refresh
    token, persist it, and live-connect the Outlook client."""
    if error or not code:
        detail = error_description or error or "no code"
        return HTMLResponse(
            _oauth_result_page(f"Microsoft sign-in failed: {detail}", False), status_code=400
        )

    from app.mcp.microsoft_rest import _Token, exchange_code

    try:
        tok = await exchange_code(code)
    except httpx.HTTPStatusError as e:
        logger.error(
            "microsoft_oauth_exchange_failed",
            extra={"status": e.response.status_code, "body": e.response.text[:300]},
        )
        return HTMLResponse(
            _oauth_result_page(f"Token exchange failed: {e.response.text[:200]}", False),
            status_code=400,
        )
    except Exception as e:  # noqa: BLE001
        logger.error("microsoft_oauth_exchange_failed", extra={"error": str(e)})
        return HTMLResponse(_oauth_result_page(f"Token exchange failed: {e}", False), status_code=400)

    refresh = tok.get("refresh_token")
    if not refresh:
        # Almost always a missing offline_access scope on the app registration.
        return HTMLResponse(
            _oauth_result_page(
                "Microsoft returned no refresh token — check that the app "
                "registration grants the offline_access delegated permission.", False
            ),
            status_code=400,
        )

    from app.core.runtime_state import set_microsoft_refresh_token
    set_microsoft_refresh_token(refresh)
    # A stale access token from a previous connection would otherwise be served
    # from the cache until it expired, under the OLD account's identity.
    _Token.clear()

    try:
        from app.mcp.servers import build_microsoft_clients
        registry = request.app.state.registry
        n = await registry.reconnect_mcp_servers(build_microsoft_clients())
        logger.info("microsoft_connected_via_ui", extra={"servers": n})
        return HTMLResponse(_oauth_result_page(f"Microsoft 365 connected — {n} service(s) live.", True))
    except Exception as e:  # noqa: BLE001
        logger.error("microsoft_live_connect_failed", extra={"error": str(e)})
        return HTMLResponse(_oauth_result_page("Microsoft signed in (restart to activate).", True))


# ── Notion one-click sign-in ────────────────────────────────────────────────

@router.get("/connections/notion/login")
async def notion_login():
    """Return the Notion consent URL for the 'Sign in with Notion' button."""
    if not settings.notion_client_id or not settings.notion_client_secret:
        return {
            "error": "Notion OAuth client not configured. Set NOTION_CLIENT_ID and "
                     "NOTION_CLIENT_SECRET in the backend .env (one-time, in Notion "
                     "My Integrations → Public Integration).",
        }
    params = {
        "client_id": settings.notion_client_id,
        "redirect_uri": settings.notion_oauth_redirect,
        "response_type": "code",
        "owner": "user",
    }
    return {"auth_url": "https://api.notion.com/v1/oauth/authorize?" + urllib.parse.urlencode(params)}


@router.get("/connections/notion/status")
async def notion_status():
    """Whether Notion is connected (an access token is stored)."""
    from app.core.runtime_state import get_notion_access_token
    return {"connected": bool(get_notion_access_token())}


@router.post("/connections/notion/disconnect")
async def notion_disconnect():
    from app.core.runtime_state import set_notion_access_token
    set_notion_access_token("")
    logger.info("notion_disconnected")
    return {"connected": False}


@router.get("/oauth/notion/callback", response_class=HTMLResponse)
async def notion_callback(request: Request, code: str = "", error: str = ""):
    page = _oauth_result_page

    if error or not code:
        return HTMLResponse(page(f"Notion sign-in failed: {error or 'no code'}", False), status_code=400)

    try:
        import base64
        creds = f"{settings.notion_client_id}:{settings.notion_client_secret}"
        encoded_creds = base64.b64encode(creds.encode("utf-8")).decode("utf-8")
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.notion.com/v1/oauth/token",
                headers={
                    "Authorization": f"Basic {encoded_creds}",
                    "Content-Type": "application/json",
                },
                json={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": settings.notion_oauth_redirect,
                },
                timeout=15.0,
            )
            resp.raise_for_status()
            tok = resp.json()
    except Exception as e:
        logger.error("notion_oauth_exchange_failed", extra={"error": str(e)})
        return HTMLResponse(page(f"Token exchange failed: {e}", False), status_code=400)

    access = tok.get("access_token")
    if not access:
        return HTMLResponse(page("Notion returned no access token (try again).", False), status_code=400)

    from app.core.runtime_state import set_notion_access_token
    set_notion_access_token(access)

    # Live-connect the Notion MCP server so tools work without a restart.
    try:
        from app.mcp.servers import build_notion_client
        registry = request.app.state.registry
        n = await registry.reconnect_mcp_servers([build_notion_client(access)])
        logger.info("notion_connected_via_ui", extra={"tools": n})
        return HTMLResponse(page(f"Notion connected — tools are live!", True))
    except Exception as e:  # noqa: BLE001
        logger.error("notion_live_connect_failed", extra={"error": str(e)})
        # Token is saved; a restart will pick it up even if live connect failed.
        return HTMLResponse(page("Notion signed in (restart to activate).", True))
