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

logger = logging.getLogger(__name__)
router = APIRouter(tags=["connections"])

# Scopes for the Google services SPEDA uses.
_GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/contacts.readonly",
]

# Human-friendly metadata for known servers (label + what credential it needs).
_INFO = {
    "tavily":           {"label": "Tavily — Web Search", "needs": "TAVILY_API_KEY"},
    "exa":              {"label": "Exa — Deep Search", "needs": "EXA_API_KEY"},
    "notion":           {"label": "Notion", "needs": "NOTION_API_KEY"},
    "alpha_vantage":    {"label": "Alpha Vantage — Finance", "needs": "ALPHA_VANTAGE_API_KEY"},
    "github":           {"label": "GitHub", "needs": "GITHUB_TOKEN"},
    "brave_search":     {"label": "Brave Search", "needs": "BRAVE_SEARCH_API_KEY"},
    "fetch":            {"label": "Fetch — Read Pages", "needs": None},
    "filesystem":       {"label": "Filesystem", "needs": None},
    "arxiv":            {"label": "arXiv — Papers", "needs": None},
    "cve_intelligence": {"label": "CVE Intelligence", "needs": None},
    "google_gmail":     {"label": "Google — Gmail", "needs": "GOOGLE_*"},
    "google_calendar":  {"label": "Google — Calendar", "needs": "GOOGLE_*"},
    "google_drive":     {"label": "Google — Drive", "needs": "GOOGLE_*"},
    "google_chat":      {"label": "Google — Chat", "needs": "GOOGLE_*"},
    "google_people":    {"label": "Google — Contacts", "needs": "GOOGLE_*"},
}


@router.get("/connections")
async def list_connections(request: Request):
    """Loaded MCP servers with status + a live prefix-budget estimate."""
    registry = request.app.state.registry
    rows = registry.server_summary()
    for r in rows:
        meta = _INFO.get(r["server"], {})
        r["label"] = meta.get("label", r["server"])
        r["needs"] = meta.get("needs")
    active_tokens = sum(r["tokens"] for r in rows if r["active"])
    return {
        "servers": rows,
        # Tier-1 Sonnet ITPM is 30k; the cached cold-write must fit under it.
        "active_tool_tokens": active_tokens,
        "itpm_limit": 30000,
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


@router.get("/oauth/google/callback", response_class=HTMLResponse)
async def google_callback(request: Request, code: str = "", error: str = ""):
    """Google redirects here after consent. Exchange the code for a refresh
    token, persist it, and live-connect the Google MCP servers."""
    def page(msg: str, ok: bool) -> str:
        color = "#4fa377" if ok else "#c84a3a"
        return f"""<!doctype html><html><body style="background:#06121a;color:#cadbe2;
        font-family:system-ui;display:flex;align-items:center;justify-content:center;
        height:100vh;margin:0"><div style="text-align:center">
        <div style="font-size:2rem;color:{color}">{'✓' if ok else '✕'}</div>
        <h2>{msg}</h2><p style="color:#7a96a1">You can close this tab and return to SPEDA.</p>
        </div></body></html>"""

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
