"""Connections — view/toggle MCP servers from the Settings UI.

Toggling a server hides/shows its tools live (no restart), which shrinks or
grows the cached prompt prefix — the lever for staying under the ITPM limit.
"""

import logging

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)
router = APIRouter(tags=["connections"])

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
