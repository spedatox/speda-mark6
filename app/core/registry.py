import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.adapters.base import OSSAdapter
    from app.core.context import AgentContext
    from app.mcp.client import MCPClient
    from app.skills.base import Skill

logger = logging.getLogger(__name__)

# The Task tool definition (Tier 0 — Anthropic Agent SDK built-in).
# Registered first at startup, before all other tiers.
# Full Agent SDK integration to be wired once SDK session management is confirmed.
_TASK_TOOL_DEFINITION: dict = {
    "name": "Task",
    "description": (
        "Spawns an isolated sub-agent to work on a focused task in parallel with other sub-agents. "
        "Use this when a task requires 3+ independent information sources, deep multi-source research, "
        "or when intermediate results would significantly bloat this context window. "
        "Do not use this for lookups, writes, reminders, or tasks completable in 1–3 tool calls. "
        "Returns the sub-agent's synthesised result as a string."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "Clear, scoped task description for the sub-agent.",
            },
            "prompt": {
                "type": "string",
                "description": "Full prompt to send to the sub-agent.",
            },
        },
        "required": ["description", "prompt"],
    },
}


class CapabilityRegistry:
    """
    Single plug-in interface for all four capability tiers.
    AgentOrchestrator calls list_tools() — it never hardcodes tool definitions.
    Adding a capability = register it here. The orchestrator never changes.

    Startup registration order (non-negotiable):
      Tier 0 (Task) → Tier 1 (Skills) → Tier 2 (MCP) → Tier 3 (Adapters)
    """

    def __init__(self) -> None:
        self._task_tool_registered = False
        self._skills: dict[str, "Skill"] = {}
        self._mcp_clients: dict[str, "MCPClient"] = {}
        self._mcp_tool_map: dict[str, str] = {}  # tool_name → server_name
        self._adapters: dict[str, "OSSAdapter"] = {}

    # ── Tier 0 ────────────────────────────────────────────────────────────────

    def register_task_tool(self) -> None:
        """Register the SDK built-in Task tool. Must be called FIRST."""
        self._task_tool_registered = True
        logger.info("registry_register", extra={"tier": 0, "name": "Task"})

    # ── Tier 1 ────────────────────────────────────────────────────────────────

    async def register_skill(self, skill: "Skill") -> None:
        self._skills[skill.name] = skill
        logger.info("registry_register", extra={"tier": 1, "name": skill.name})

    # ── Tier 2 ────────────────────────────────────────────────────────────────

    async def register_mcp(self, client: "MCPClient") -> None:
        await client.connect()
        self._mcp_clients[client.server_name] = client
        tools = await client.list_tools()
        for tool in tools:
            self._mcp_tool_map[tool["name"]] = client.server_name
        logger.info(
            "registry_register",
            extra={"tier": 2, "name": client.server_name, "tools": len(tools)},
        )

    # ── Tier 3 ────────────────────────────────────────────────────────────────

    async def register_adapter(self, adapter: "OSSAdapter") -> None:
        self._adapters[adapter.name] = adapter
        logger.info("registry_register", extra={"tier": 3, "name": adapter.name})

    # ── Unified interface ──────────────────────────────────────────────────────

    def list_tools(self) -> list[dict]:
        """
        Return all tools across all four tiers in Anthropic tool format.
        Claude sees no difference between tiers.
        """
        tools: list[dict] = []

        if self._task_tool_registered:
            tools.append(_TASK_TOOL_DEFINITION)

        for skill in self._skills.values():
            tools.append(skill.to_tool_definition())

        for client in self._mcp_clients.values():
            # MCP tools were loaded at connect time and cached
            pass  # TODO: extend once MCPClient.list_tools() returns live data

        for adapter in self._adapters.values():
            tools.append(adapter.to_tool_definition())

        return tools

    async def execute(self, tool_name: str, args: dict, context: "AgentContext") -> str:
        """Route a tool call to the correct tier handler."""
        try:
            if tool_name == "Task":
                return await self._execute_task(args, context)

            if tool_name in self._skills:
                return await self._skills[tool_name].execute(args, context)

            if tool_name in self._mcp_tool_map:
                server_name = self._mcp_tool_map[tool_name]
                return await self._mcp_clients[server_name].call_tool(tool_name, args)

            if tool_name in self._adapters:
                return await self._adapters[tool_name].execute(args, context)

            return f"Unknown tool: '{tool_name}'"

        except Exception as e:
            logger.error(
                "tool_execution_error",
                extra={
                    "tool": tool_name,
                    "request_id": context.request_id,
                    "error": str(e),
                },
            )
            return f"Error executing {tool_name}: {str(e)}"

    async def _execute_task(self, args: dict, context: "AgentContext") -> str:
        """
        Tier 0 — Task sub-agent execution.
        TODO: Wire to Anthropic Agent SDK once session management is confirmed.
        """
        logger.info(
            "task_tool_called",
            extra={
                "request_id": context.request_id,
                "description": args.get("description", ""),
            },
        )
        return (
            "Task sub-agent (Tier 0) called but Agent SDK integration is pending. "
            f"Task description: {args.get('description', 'none provided')}"
        )

    async def health_check_all(self) -> dict:
        """Check health of all Tier 3 adapters. Called at startup and by Ratchet."""
        results: dict[str, bool] = {}
        for name, adapter in self._adapters.items():
            results[name] = await adapter.health_check()
        return results

    async def shutdown_adapters(self) -> None:
        """Disconnect all MCP clients on shutdown."""
        for client in self._mcp_clients.values():
            await client.disconnect()
