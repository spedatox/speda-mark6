import logging
import os
from contextlib import AsyncExitStack
from typing import Literal

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import Tool as MCPTool

logger = logging.getLogger(__name__)


def _mcp_tool_to_anthropic(tool: MCPTool) -> dict:
    """Convert MCP tool format to Anthropic tool definition format."""
    schema = tool.inputSchema if tool.inputSchema else {"type": "object", "properties": {}}
    return {
        "name": tool.name,
        "description": tool.description or "",
        "input_schema": schema,
    }


class MCPClient:
    """
    Wraps a single MCP server connection.
    Tier 2 capability — used for third-party integrations with existing MCP servers.

    Transport:
      - stdio: server runs as a local subprocess on Contabo (low-latency, no network)
      - http:  remote server over Streamable HTTP with optional auth headers

    Lifecycle is managed via AsyncExitStack: connect() opens the context,
    disconnect() closes it. Both are called by CapabilityRegistry.
    """

    def __init__(
        self,
        server_name: str,
        transport: Literal["stdio", "http"],
        command: list[str] | None = None,
        url: str | None = None,
        env: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.server_name = server_name
        self.transport = transport
        self.command = command      # stdio only: [executable, *args]
        self.url = url              # http only: full base URL
        self.env = env or {}        # stdio only: merged into subprocess env
        self.headers = headers or {}  # http only: auth / custom headers
        self._tools: list[dict] = []
        self._connected = False
        self._session: ClientSession | None = None
        self._exit_stack = AsyncExitStack()

    async def connect(self) -> None:
        """Establish connection to the MCP server and load its tool list."""
        try:
            if self.transport == "stdio":
                await self._connect_stdio()
            else:
                await self._connect_http()

            tools_response = await self._session.list_tools()
            self._tools = [_mcp_tool_to_anthropic(t) for t in tools_response.tools]
            self._connected = True
            logger.info(
                "mcp_connected",
                extra={
                    "server": self.server_name,
                    "transport": self.transport,
                    "tools": len(self._tools),
                },
            )
        except BaseException as e:
            # Re-raise real signals; swallow everything else (including
            # CancelledError / anyio cancel-scope errors from HTTP transports
            # that return 4xx — these are BaseException, not Exception).
            if isinstance(e, (KeyboardInterrupt, SystemExit)):
                raise
            logger.warning(
                "mcp_connect_failed",
                extra={"server": self.server_name, "error": str(e)},
            )

    async def _connect_stdio(self) -> None:
        if not self.command:
            raise ValueError(f"stdio transport requires a command for server '{self.server_name}'")
        params = StdioServerParameters(
            command=self.command[0],
            args=self.command[1:],
            env={**os.environ, **self.env},
        )
        read, write = await self._exit_stack.enter_async_context(stdio_client(params))
        self._session = await self._exit_stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()

    async def _connect_http(self) -> None:
        from mcp.client.streamable_http import streamablehttp_client

        if not self.url:
            raise ValueError(f"http transport requires a url for server '{self.server_name}'")
        read, write, _ = await self._exit_stack.enter_async_context(
            streamablehttp_client(self.url, headers=self.headers)
        )
        self._session = await self._exit_stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()

    async def list_tools(self) -> list[dict]:
        """Return all tools from this MCP server in Anthropic tool format."""
        return self._tools

    async def call_tool(self, tool_name: str, args: dict) -> str:
        """Call a tool on this MCP server and return the result as a string."""
        if not self._connected or self._session is None:
            return f"MCP server '{self.server_name}' is not connected."
        result = await self._session.call_tool(tool_name, arguments=args)
        parts = [
            block.text
            for block in result.content
            if hasattr(block, "text") and block.text
        ]
        return "\n".join(parts) if parts else "(no output)"

    async def disconnect(self) -> None:
        try:
            await self._exit_stack.aclose()
        except Exception as e:
            # MCP stdio servers are entered in the lifespan startup task and torn
            # down in shutdown; anyio raises a "cancel scope in a different task"
            # RuntimeError when the task group is closed across that boundary
            # (very visible under --reload). The subprocess is killed regardless,
            # so swallow the cleanup noise instead of failing app shutdown.
            logger.debug(
                "mcp_disconnect_cleanup_ignored",
                extra={"server": self.server_name, "error": str(e)},
            )
        finally:
            self._connected = False
            self._session = None
        logger.info("mcp_disconnected", extra={"server": self.server_name})
