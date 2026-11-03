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
from typing import TYPE_CHECKING

from app.config import settings
from app.mcp.client import MCPClient

if TYPE_CHECKING:
    from app.core.registry import CapabilityRegistry

logger = logging.getLogger(__name__)


def pin_builtin_command(command):
    import json
    import re
    pins = json.loads(settings.mcp_package_pins)
    package = command[2] if command[0] == 'npx' else command[1]
    package = re.sub(r'@\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?$', '', package) if command[0] == 'npx' else package.split('==')[0]
    version = pins.get(package, '')
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?', version):
        raise ValueError('An exact operator-approved MCP package pin is required')
    result = list(command)
    result[2 if command[0] == 'npx' else 1] = package + ('@' if command[0] == 'npx' else '==') + version
    return result


class NotionMCPClient(MCPClient):
    def __init__(self, token_provider):
        self.token_provider = token_provider
        super().__init__('notion', command=['npx','-y','@notionhq/notion-mcp-server'])
        self.allowed_users = frozenset({1})

    async def connect(self):
        token = await self.token_provider.get()
        if not token:
            raise ValueError('Notion needs authentication')
        self.command = pin_builtin_command(['npx','-y','@notionhq/notion-mcp-server'])
        self.env = {'NOTION_TOKEN':token}
        await super().connect()

    async def _check_credential(self):
        from app.schemas.llm import ProviderError
        if await self.token_provider.get() != self.env.get('NOTION_TOKEN'):
            self._connected = False
            raise ProviderError('credential_changed', 'Notion credentials changed; reconnect before starting a new turn')

    async def call_tool(self, *args, **kwargs):
        await self._check_credential()
        return await super().call_tool(*args, **kwargs)

    async def request(self, *args, **kwargs):
        await self._check_credential()
        return await super().request(*args, **kwargs)


def build_notion_client(token_provider=None):
    from app.services.notion_auth import NotionToken
    return NotionMCPClient(token_provider or NotionToken())


async def register_all_mcp_servers(registry: "CapabilityRegistry", tokens=None, active=None) -> None:
    servers: list[MCPClient] = []

    from app.core.runtime_state import get_notion_credentials
    if get_notion_credentials().get('access_token'):
        servers.append(build_notion_client((tokens or {}).get('notion')))
    else:
        logger.warning("mcp_skip", extra={"server": "notion", "reason": "NOTION_API_KEY not set"})

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

    if settings.alpha_vantage_api_key:
        servers.append(
            MCPClient(
                server_name="alpha_vantage",
                transport="http",
                url=f"https://mcp.alphavantage.co/mcp?apikey={settings.alpha_vantage_api_key}",
            )
        )
    else:
        logger.warning("mcp_skip", extra={"server": "alpha_vantage", "reason": "ALPHA_VANTAGE_API_KEY not set"})

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

    # Filesystem — no API key, path sandboxed to /tmp/speda_outputs
    servers.append(
        MCPClient(
            server_name="filesystem",
            transport="stdio",
            command=[
                "npx", "-y", "@modelcontextprotocol/server-filesystem",
                "/tmp/speda_outputs",
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

    # Playwright — MUST run in isolated Docker container (CVE-2025-9611)
    # Registered as http transport pointing to the isolated container's internal port
    if settings.playwright_mcp_url:
      servers.append(
        MCPClient(
            server_name="playwright",
            transport="http",
            url=settings.playwright_mcp_url,
        )
    )

    # Preserve existing owner-connected vendor services without their legacy
    # registry/transport shell. No sign-in or consent is initiated at startup.
    from app.core.runtime_state import get_google_refresh_token, get_microsoft_refresh_token
    if get_google_refresh_token():
        from app.mcp.google_rest import build_google_clients
        servers.extend(build_google_clients(token_provider=(tokens or {}).get('google')))
    if get_microsoft_refresh_token():
        from app.mcp.microsoft_rest import build_microsoft_clients
        servers.extend(build_microsoft_clients(token_provider=(tokens or {}).get('microsoft')))

    # Preserve explicit deployment enablement; unavailable connections degrade
    # independently and cannot leave cancellation scopes in the lifespan task.
    enabled = {s.strip() for s in settings.mcp_enabled.split(',') if s.strip()}
    all_agents = frozenset({'speda', 'ultron', 'optimus', 'friday', 'atomix', 'scourge', 'sentinel', 'orion'})
    registry._configured_mcp = {server.server_name: server for server in servers}
    registry._mcp_startup_errors = {}
    for server in servers:
        server.allowed_agents = all_agents if server.server_name != 'filesystem' else frozenset({'speda','optimus','scourge','orion'})
        server.allowed_users = frozenset({1})
        if 'all' not in enabled and server.server_name not in enabled:
            continue
        if not (active or {}).get(server.server_name, True):
            continue
        try:
            if server.transport == 'stdio' and not isinstance(server, NotionMCPClient):
                server.command = pin_builtin_command(server.command)
            await registry.register_mcp(server)
        except Exception as exc:
            registry._mcp_startup_errors[server.server_name] = 'Connection unavailable; verify credentials, exact package pin and isolated runtime'
            logger.warning('mcp_unavailable', extra={'server': server.server_name, 'error_type': type(exc).__name__})
    if settings.mcp_connections_file:
        from app.mcp.configuration import load_connections
        for server in load_connections(settings.mcp_connections_file):
            if server.server_name in registry._configured_mcp:
                raise ValueError('Operator MCP identity conflicts with a built-in server')
            registry._configured_mcp[server.server_name] = server
            if not (active or {}).get(server.server_name, True):
                continue
            try:
                await registry.register_mcp(server)
            except Exception as exc:
                logger.warning('mcp_unavailable', extra={'server': server.server_name, 'error_type': type(exc).__name__})
