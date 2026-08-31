# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
MCP server registrations for Speda Mark VI.

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
  12. Playwright     — open-public-web browser automation, full upstream tool
                       parity (MUST run in isolated container; never for the
                       owner's saved logins — that's the browser sidecar)
"""

import logging
from typing import TYPE_CHECKING

from app.config import settings, _DATA_DIR
from app.mcp.client import MCPClient

if TYPE_CHECKING:
    from app.core.registry import CapabilityRegistry

logger = logging.getLogger(__name__)


def build_google_clients(access_token: str | None = None):
    """Build the Google Workspace clients.

    These now talk to the STANDARD Google REST APIs (gmail.googleapis.com, …)
    rather than Google's gated preview MCP endpoints (gmailmcp.googleapis.com),
    which blanket-deny "caller does not have permission" outside the Developer
    Preview Program even with a valid token. The REST clients duck-type the
    MCPClient surface the registry drives, so registration / lazy loading / the
    Connections panel / sign-in flow are unchanged. See app/mcp/google_rest.py.
    The access_token arg is ignored (clients self-refresh) but kept so existing
    callers (connections.py) don't change.
    """
    from app.mcp.google_rest import build_google_clients as _build_rest

    return _build_rest(access_token)


def build_microsoft_clients():
    """Build the Microsoft 365 (Outlook) client.

    Talks to the standard Graph REST API and duck-types the MCPClient surface the
    registry drives, exactly like the Google clients — so registration, lazy
    loading, the Connections panel and the sign-in flow are unchanged. See
    app/mcp/microsoft_rest.py.
    """
    from app.mcp.microsoft_rest import build_microsoft_clients as _build_ms

    return _build_ms()


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


def build_notion_client(access_token: str) -> MCPClient:
    """Build the official Notion MCP server client (@notionhq/notion-mcp-server).

    This is Notion's open-source server (v2.x — data-source era tools), run as a
    local stdio subprocess. Auth goes in the NOTION_TOKEN env var, which accepts
    any Notion bearer token — including the OAuth access token captured by the
    in-app sign-in flow. Notion's hosted endpoint (mcp.notion.com/mcp) is
    interactive-OAuth-only (no bearer/headless auth), so the local official
    server IS the native connection for a backend like this.
    """
    return MCPClient(
        server_name="notion",
        transport="stdio",
        command=["npx", "-y", "@notionhq/notion-mcp-server"],
        env={"NOTION_TOKEN": access_token},
    )


# Names the owner may not claim for a hand-added server: every server this module
# builds. Shadowing one would produce two clients with the same server_name, and
# the registry's tool→server map keeps only the last — so the built-in's tools
# would still be listed while every call routed somewhere else.
RESERVED_SERVER_NAMES = frozenset({
    "notion", "alpha_vantage", "playwright", "brave_search", "fetch", "tavily",
    "exa", "github", "filesystem", "arxiv", "cve_intelligence",
    "google_gmail", "google_calendar", "google_tasks", "google_drive",
    "google_chat", "google_people", "microsoft_outlook",
})


def build_custom_client(record: dict) -> MCPClient:
    """One MCPClient from a stored owner-defined server record.

    This is the whole reason MCP is worth having as a tier: a server is a command
    or a URL plus credentials, so anything speaking the protocol can be added
    without a code change. See app/core/runtime_state.py for the record shape.
    """
    transport = (record.get("transport") or "stdio").strip().lower()
    if transport == "http":
        return MCPClient(
            server_name=record["name"],
            transport="http",
            url=record.get("url") or "",
            headers=dict(record.get("headers") or {}),
        )
    command = record.get("command") or []
    if isinstance(command, str):
        # Accept a pasted command line — that is how every MCP README writes it.
        import shlex

        command = shlex.split(command)
    return MCPClient(
        server_name=record["name"],
        transport="stdio",
        command=list(command),
        env=dict(record.get("env") or {}),
    )


def build_custom_clients() -> list[MCPClient]:
    """Every enabled owner-defined server. A record that cannot be turned into a
    client is skipped and logged rather than taking startup down with it — the
    same degraded-not-fatal posture as a missing API key."""
    from app.core.runtime_state import get_custom_mcp_servers

    clients: list[MCPClient] = []
    for record in get_custom_mcp_servers():
        if not record.get("enabled", True):
            continue
        try:
            clients.append(build_custom_client(record))
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "custom_mcp_build_failed",
                extra={"server": record.get("name", "?"), "error": str(e)},
            )
    return clients


async def register_all_mcp_servers(registry: "CapabilityRegistry") -> None:
    servers: list[MCPClient] = []

    # ── Tier 2: HTTP servers (auth via headers) ──────────────────────────────

    # Notion — official local MCP server, authed with the OAuth access token
    # from the UI sign-in flow. See build_notion_client().
    from app.core.runtime_state import get_notion_access_token
    notion_access = get_notion_access_token()
    notion_ready = all([
        settings.notion_client_id,
        settings.notion_client_secret,
        notion_access,
    ])

    if notion_ready:
        servers.append(build_notion_client(notion_access))
    else:
        logger.warning("mcp_skip", extra={"server": "notion", "reason": "NOTION_CLIENT_ID, SECRET, or access token not set (needs OAuth)"})

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

    # Playwright — the SECOND, separate browser path, for the OPEN PUBLIC WEB
    # ONLY (packages/playwright-mcp, its own isolated container, never a
    # published port — see docker-compose.yml and docs/BROWSER.md). This is
    # NOT a replacement for the browser sidecar (packages/browser, reached
    # through app/services/browser.py as browse_page / browser_act /
    # portal_login) — that stays the ONLY path for anything touching one of
    # the owner's saved logins, for one reason above all: a login through this
    # MCP server means the MODEL types the owner's password, which puts it in
    # the transcript, the message table and the embedding index. See the
    # module docstring of packages/browser/server.py, and the steering note in
    # app/prompts/core/03_capabilities.md that tells the model which path is
    # for what. Off entirely when playwright_mcp_url is unset (the default).
    if settings.playwright_mcp_url:
        servers.append(
            MCPClient(
                server_name="playwright",
                transport="http",
                url=settings.playwright_mcp_url,
            )
        )
    else:
        logger.info("mcp_skip", extra={"server": "playwright", "reason": "playwright_mcp_url not set"})

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

    # Filesystem — sandboxed to the user's Speda outputs directory
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

    # ── Google Workspace — STANDARD REST APIs (see app/mcp/google_rest.py) ───
    # Gmail / Calendar / Drive / Contacts via gmail.googleapis.com etc. The REST
    # clients self-refresh their access token on demand, so no startup token
    # exchange is needed and a long-running session no longer dies after ~1h.
    # Registration just needs the OAuth client + a stored refresh token; each
    # client's connect() validates the token can actually be obtained.
    from app.core.runtime_state import get_google_refresh_token
    google_refresh = get_google_refresh_token()  # UI sign-in token, or .env fallback
    google_ready = all([
        settings.google_client_id,
        settings.google_client_secret,
        google_refresh,
    ])
    if google_ready:
        servers.extend(build_google_clients())
    else:
        logger.warning("mcp_skip", extra={
            "server": "google_workspace",
            "reason": "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REFRESH_TOKEN not all set",
        })

    # ── Microsoft 365 — Graph REST (see app/mcp/microsoft_rest.py) ───────────
    # The owner's university mailbox. Separate estate from Gmail, separate OAuth
    # client, same registration shape: the client self-refreshes its access token
    # on demand, so startup only needs the app registration + a stored refresh
    # token, and connect() proves the token can actually be obtained.
    from app.core.runtime_state import get_microsoft_refresh_token
    microsoft_ready = all([
        settings.microsoft_client_id,
        settings.microsoft_client_secret,
        get_microsoft_refresh_token(),
    ])
    if microsoft_ready:
        servers.extend(build_microsoft_clients())
    else:
        logger.warning("mcp_skip", extra={
            "server": "microsoft_outlook",
            "reason": "MICROSOFT_CLIENT_ID / MICROSOFT_CLIENT_SECRET / refresh token not all set",
        })

    # ── Owner-defined MCP servers (Settings → Connections → Add server) ──────
    # Anything the owner wired by hand: a command for stdio, or a URL plus
    # headers for streamable HTTP. Registered last so a hand-added server can
    # deliberately shadow nothing — a name collision with a built-in is rejected
    # at save time, not silently here. See app/core/runtime_state.py.
    custom = build_custom_clients()
    servers.extend(custom)

    # ── Gate by the configured allowlist ─────────────────────────────────────
    # Every loaded tool inflates the cached prompt prefix on every request, so
    # only register the servers the operator enabled. "all" = no filtering.
    enabled_raw = (settings.mcp_enabled or "").strip().lower()
    if enabled_raw and enabled_raw != "all":
        enabled = {name.strip() for name in enabled_raw.split(",") if name.strip()}
        custom_names = {c.server_name for c in custom}

        def _is_enabled(server_name: str) -> bool:
            # A server the owner added by hand is already an explicit opt-in;
            # making them ALSO name it in MCP_ENABLED means "I added it and
            # nothing happened", which is the worst outcome this list can produce.
            if server_name in custom_names:
                return True
            # google_gmail / google_calendar / … all match the "google" alias,
            # microsoft_outlook matches "microsoft".
            prefix = server_name.split("_", 1)[0]
            if "_" in server_name and prefix in enabled:
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
