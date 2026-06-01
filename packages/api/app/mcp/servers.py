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


async def register_all_mcp_servers(registry: "CapabilityRegistry") -> None:
    servers: list[MCPClient] = []

    # ── Tier 2: HTTP servers (auth via headers) ──────────────────────────────

    if settings.notion_api_key:
        servers.append(
            MCPClient(
                server_name="notion",
                transport="http",
                url="https://mcp.notion.com",
                headers={"Authorization": f"Bearer {settings.notion_api_key}"},
            )
        )
    else:
        logger.warning("mcp_skip", extra={"server": "notion", "reason": "NOTION_API_KEY not set"})

    if settings.alpha_vantage_api_key:
        servers.append(
            MCPClient(
                server_name="alpha_vantage",
                transport="http",
                url="https://mcp.alphavantage.co",
                headers={"Authorization": f"Bearer {settings.alpha_vantage_api_key}"},
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

    # ── Google Workspace — Gmail + Calendar + Drive ──────────────────────────
    # Requires OAuth credentials.json from Google Cloud Console.
    # Run the one-time auth flow first:
    #   npx -y @aaronsb/google-workspace-mcp auth
    # Then set GOOGLE_CREDENTIALS_PATH in .env pointing to your credentials.json.
    if settings.google_credentials_path:
        import pathlib
        creds_path = pathlib.Path(settings.google_credentials_path)
        tokens_dir = pathlib.Path(settings.google_tokens_dir)
        tokens_dir.mkdir(parents=True, exist_ok=True)
        if creds_path.exists():
            servers.append(
                MCPClient(
                    server_name="google_workspace",
                    transport="stdio",
                    command=["npx", "-y", "@aaronsb/google-workspace-mcp"],
                    env={
                        "GOOGLE_CREDENTIALS_PATH": str(creds_path),
                        "GOOGLE_TOKENS_DIR": str(tokens_dir),
                    },
                )
            )
        else:
            logger.warning("mcp_skip", extra={
                "server": "google_workspace",
                "reason": f"credentials file not found at {creds_path}",
            })
    else:
        logger.warning("mcp_skip", extra={
            "server": "google_workspace",
            "reason": "GOOGLE_CREDENTIALS_PATH not set",
        })

    for server in servers:
        await registry.register_mcp(server)
