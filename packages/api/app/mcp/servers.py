"""
MCP server registrations for SPEDA Mark VI.

All 12 confirmed servers from Entry 005. Each is registered here and added to the
CapabilityRegistry at startup. If a required API key is missing, the server is skipped
and logged as degraded — startup continues (non-fatal per CLAUDE.md).

Startup registration order (Entry 005 priority):
  1. Notion          — already connected, R&D log live
  2. Google Workspace — Gmail + Calendar
  3. Brave Search    — web search fallback
  4. Fetch           — web content → Markdown
  5. Alpha Vantage   — Sentinel financial data
  6. Tavily          — NightCrawler primary search
  7. Exa             — NightCrawler + Ultron research
  8. GitHub          — Optimus + Ultron engineering
  9. Filesystem      — Optimus local file ops
  10. arXiv          — Ultron + NightCrawler academic
  11. CVE Intelligence — Unicron security intelligence
  12. Playwright     — NightCrawler browser automation (MUST run in isolated container)
"""

import logging
import os
from typing import TYPE_CHECKING

from app.config import settings, _DATA_DIR
from app.mcp.client import MCPClient

if TYPE_CHECKING:
    from app.core.registry import CapabilityRegistry

logger = logging.getLogger(__name__)


# Official Google Workspace remote MCP endpoints.
GOOGLE_SERVICES = [
    ("google_gmail",    "https://gmailmcp.googleapis.com/mcp/v1"),
    ("google_calendar", "https://calendarmcp.googleapis.com/mcp/v1"),
    ("google_drive",    "https://drivemcp.googleapis.com/mcp/v1"),
    ("google_chat",     "https://chatmcp.googleapis.com/mcp/v1"),
    ("google_people",   "https://people.googleapis.com/mcp/v1"),
]


def build_google_clients(access_token: str) -> list[MCPClient]:
    """Build the Google Workspace MCP clients for a given access token."""
    auth = {"Authorization": f"Bearer {access_token}"}
    return [
        MCPClient(server_name=name, transport="http", url=url, headers=auth)
        for name, url in GOOGLE_SERVICES
    ]


async def _refresh_google_token(client_id: str, client_secret: str, refresh_token: str) -> str | None:
    """Exchange a Google OAuth refresh token for a fresh access token."""
    try:
        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
                timeout=10.0,
            )
            resp.raise_for_status()
            token = resp.json().get("access_token")
            if token:
                logger.info("google_token_refreshed")
            return token
    except Exception as e:
        logger.error("google_token_refresh_failed", extra={"error": str(e)})
        return None


async def register_all_mcp_servers(registry: "CapabilityRegistry") -> None:
    servers: list[MCPClient] = []

    # ── Tier 2: HTTP servers (auth via headers) ──────────────────────────────

    # Notion — official stdio package (integration token auth).
    # The hosted mcp.notion.com/mcp requires full OAuth; the npm package
    # works with a standard Notion integration token (starts with ntn_ or secret_).
    # Create an integration at https://www.notion.so/profile/integrations
    # and connect it to the pages/databases you want SPEDA to access.
    if settings.notion_api_key:
        # @notionhq/notion-mcp-server passes OPENAPI_MCP_HEADERS straight to the
        # Notion REST API, which REQUIRES a Notion-Version header on every call.
        # Sending only Authorization makes Notion reject every request with a
        # "Notion-Version header should be included" 400 — pages can't be
        # listed/searched even though auth itself is valid. Pin the current
        # stable API version alongside the token.
        import json as _json

        notion_headers = _json.dumps({
            "Authorization": f"Bearer {settings.notion_api_key}",
            "Notion-Version": settings.notion_version,
        })
        servers.append(
            MCPClient(
                server_name="notion",
                transport="stdio",
                command=["npx", "-y", "@notionhq/notion-mcp-server"],
                env={"OPENAPI_MCP_HEADERS": notion_headers},
            )
        )
    else:
        logger.warning("mcp_skip", extra={"server": "notion", "reason": "NOTION_API_KEY not set"})

    if settings.alpha_vantage_api_key:
        servers.append(
            MCPClient(
                server_name="alpha_vantage",
                transport="http",
                # API key goes in query string, not Authorization header
                url=f"https://mcp.alphavantage.co/mcp?apikey={settings.alpha_vantage_api_key}",
            )
        )
    else:
        logger.warning("mcp_skip", extra={"server": "alpha_vantage", "reason": "ALPHA_VANTAGE_API_KEY not set"})

    # Playwright — only available when running inside Docker stack
    if os.environ.get("PLAYWRIGHT_MCP_URL"):
        servers.append(
            MCPClient(
                server_name="playwright",
                transport="http",
                url=os.environ["PLAYWRIGHT_MCP_URL"],
            )
        )

    # ── Tier 2: stdio servers (auth via subprocess env) ──────────────────────

    if settings.brave_search_api_key:
        servers.append(
            MCPClient(
                server_name="brave_search",
                transport="stdio",
                command=["npx", "-y", "@brave/brave-search-mcp-server"],
                env={"BRAVE_API_KEY": settings.brave_search_api_key},
            )
        )
    else:
        logger.warning("mcp_skip", extra={"server": "brave_search", "reason": "BRAVE_SEARCH_API_KEY not set"})

    # Fetch — no API key required
    servers.append(
        MCPClient(
            server_name="fetch",
            transport="stdio",
            command=["npx", "-y", "@modelcontextprotocol/server-fetch"],
        )
    )

    if settings.tavily_api_key:
        servers.append(
            MCPClient(
                server_name="tavily",
                transport="stdio",
                command=["npx", "-y", "tavily-mcp"],
                env={"TAVILY_API_KEY": settings.tavily_api_key},
            )
        )
    else:
        logger.warning("mcp_skip", extra={"server": "tavily", "reason": "TAVILY_API_KEY not set"})

    if settings.exa_api_key:
        servers.append(
            MCPClient(
                server_name="exa",
                transport="stdio",
                command=["npx", "-y", "exa-mcp-server"],
                env={"EXA_API_KEY": settings.exa_api_key},
            )
        )
    else:
        logger.warning("mcp_skip", extra={"server": "exa", "reason": "EXA_API_KEY not set"})

    if settings.github_token:
        servers.append(
            MCPClient(
                server_name="github",
                transport="stdio",
                command=["npx", "-y", "@modelcontextprotocol/server-github"],
                env={"GITHUB_PERSONAL_ACCESS_TOKEN": settings.github_token},
            )
        )
    else:
        logger.warning("mcp_skip", extra={"server": "github", "reason": "GITHUB_TOKEN not set"})

    # Filesystem — sandboxed to the user's SPEDA outputs directory
    outputs_dir = str(_DATA_DIR / "outputs")
    servers.append(
        MCPClient(
            server_name="filesystem",
            transport="stdio",
            command=[
                "npx", "-y", "@modelcontextprotocol/server-filesystem",
                outputs_dir,
            ],
        )
    )

    # arXiv — no API key
    servers.append(
        MCPClient(
            server_name="arxiv",
            transport="stdio",
            command=["uvx", "arxiv-mcp-server"],
        )
    )

    # CVE Intelligence — no API key
    servers.append(
        MCPClient(
            server_name="cve_intelligence",
            transport="stdio",
            command=["npx", "-y", "cve-intelligence-mcp"],
        )
    )

    # ── Google Workspace — official remote MCP servers (googleapis.com) ──────
    # Uses Google's hosted MCP endpoints — no npm package needed.
    # One-time setup: run scripts/google_oauth.py to get a refresh token,
    # then add GOOGLE_CLIENT_ID / _SECRET / _REFRESH_TOKEN to .env.
    #
    # Access tokens expire in ~1 hour; the server gets a fresh one at startup.
    # If SPEDA runs longer than that, restart the backend to re-auth.
    # (Proper background refresh is a future improvement.)
    from app.core.runtime_state import get_google_refresh_token
    google_refresh = get_google_refresh_token()  # UI sign-in token, or .env fallback
    google_ready = all([
        settings.google_client_id,
        settings.google_client_secret,
        google_refresh,
    ])
    if google_ready:
        access_token = await _refresh_google_token(
            settings.google_client_id,
            settings.google_client_secret,
            google_refresh,
        )
        if access_token:
            servers.extend(build_google_clients(access_token))
        else:
            logger.error("mcp_skip", extra={
                "server": "google_workspace",
                "reason": "OAuth token refresh failed — check GOOGLE_CLIENT_ID/SECRET/REFRESH_TOKEN",
            })
    else:
        logger.warning("mcp_skip", extra={
            "server": "google_workspace",
            "reason": "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REFRESH_TOKEN not all set",
        })

    # ── Gate by the configured allowlist ─────────────────────────────────────
    # Every loaded tool inflates the cached prompt prefix on every request, so
    # only register the servers the operator enabled. "all" = no filtering.
    enabled_raw = (settings.mcp_enabled or "").strip().lower()
    if enabled_raw and enabled_raw != "all":
        enabled = {name.strip() for name in enabled_raw.split(",") if name.strip()}

        def _is_enabled(server_name: str) -> bool:
            # google_gmail / google_calendar / … all match the "google" alias
            if server_name.startswith("google_") and "google" in enabled:
                return True
            return server_name in enabled

        kept, skipped = [], []
        for s in servers:
            (kept if _is_enabled(s.server_name) else skipped).append(s)
        for s in skipped:
            logger.info("mcp_disabled", extra={
                "server": s.server_name,
                "reason": "not in MCP_ENABLED allowlist",
            })
        servers = kept

    for server in servers:
        await registry.register_mcp(server)
