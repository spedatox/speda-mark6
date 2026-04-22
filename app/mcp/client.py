import logging
from typing import Literal

logger = logging.getLogger(__name__)


class MCPClient:
    """
    Wraps a single MCP server connection.
    Tier 2 capability — used for third-party integrations with existing MCP servers.

    Transport:
      - stdio: server runs as a local subprocess on Contabo (low-latency, no network)
      - http:  remote server over Streamable HTTP/SSE with OAuth 2.1
    """

    def __init__(
        self,
        server_name: str,
        transport: Literal["stdio", "http"],
        command: list[str] | None = None,
        url: str | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.server_name = server_name
        self.transport = transport
        self.command = command  # For stdio transport
        self.url = url          # For http transport
        self.env = env or {}
        self._tools: list[dict] = []
        self._connected = False

    async def connect(self) -> None:
        """Establish connection to the MCP server and load its tool list."""
        try:
            # TODO: Integrate mcp SDK session management.
            # For stdio: launch subprocess, create mcp.ClientSession(stdin, stdout)
            # For http: create mcp.ClientSession over Streamable HTTP transport
            # After connecting: self._tools = await self._session.list_tools()
            logger.info(
                "mcp_connect_stub",
                extra={"server": self.server_name, "transport": self.transport},
            )
            self._connected = False  # Will be True once SDK is wired
        except Exception as e:
            logger.warning(
                "mcp_connect_failed",
                extra={"server": self.server_name, "error": str(e)},
            )

    async def list_tools(self) -> list[dict]:
        """Return all tools from this MCP server in Anthropic tool format."""
        return self._tools

    async def call_tool(self, tool_name: str, args: dict) -> str:
        """Call a tool on this MCP server and return the result as a string."""
        if not self._connected:
            return f"MCP server '{self.server_name}' is not connected."
        # TODO: await self._session.call_tool(tool_name, args)
        return f"MCP tool '{tool_name}' called on '{self.server_name}' (stub)."

    async def disconnect(self) -> None:
        if self._connected:
            # TODO: await self._session.close()
            self._connected = False
            logger.info("mcp_disconnected", extra={"server": self.server_name})
