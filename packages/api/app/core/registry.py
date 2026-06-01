import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.adapters.base import OSSAdapter
    from app.core.context import AgentContext
    from app.mcp.client import MCPClient
    from app.services.anthropic_client import AnthropicClient
    from app.skills.base import Skill

logger = logging.getLogger(__name__)

_SUB_AGENT_MAX_ITERATIONS = 15  # Sub-agents are focused tasks; lower than the main loop's 30

# The Task tool definition (Tier 0 — Anthropic Agent SDK built-in).
# Registered first at startup, before all other tiers.
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


def _blocks_to_dicts(content_blocks) -> list[dict]:
    """Convert Anthropic SDK ContentBlock objects to plain dicts for message history."""
    result = []
    for block in content_blocks:
        if block.type == "text":
            result.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            result.append(
                {
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                }
            )
        else:
            try:
                result.append(block.model_dump())
            except Exception:
                result.append({"type": block.type})
    return result


class CapabilityRegistry:
    """
    Single plug-in interface for all four capability tiers.
    AgentOrchestrator calls list_tools() — it never hardcodes tool definitions.
    Adding a capability = register it here. The orchestrator never changes.

    Startup registration order (non-negotiable):
      Tier 0 (Task) → Tier 1 (Skills) → Tier 2 (MCP) → Tier 3 (Adapters)
    """

    def __init__(self, client: "AnthropicClient | None" = None) -> None:
        self._client = client            # Injected at startup — required for Task sub-agents
        self._task_tool_registered = False
        self._skills: dict[str, "Skill"] = {}
        self._mcp_clients: dict[str, "MCPClient"] = {}
        self._mcp_tool_map: dict[str, str] = {}  # tool_name → server_name
        self._mcp_tool_defs: list[dict] = []     # full definitions for list_tools()
        self._adapters: dict[str, "OSSAdapter"] = {}

    # ── Tier 0 ────────────────────────────────────────────────────────────────

    def register_task_tool(self) -> None:
        """Register the SDK built-in Task tool. Must be called FIRST."""
        self._task_tool_registered = True
        logger.info("registry_register", extra={"tier": 0, "capability": "Task"})

    # ── Tier 1 ────────────────────────────────────────────────────────────────

    async def register_skill(self, skill: "Skill") -> None:
        self._skills[skill.name] = skill
        logger.info("registry_register", extra={"tier": 1, "capability": skill.name})

    # ── Tier 2 ────────────────────────────────────────────────────────────────

    async def register_mcp(self, client: "MCPClient") -> None:
        try:
            await client.connect()
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            logger.warning(
                "mcp_connect_failed",
                extra={"server": client.server_name, "reason": str(exc)},
            )
            return
        self._mcp_clients[client.server_name] = client
        tools = await client.list_tools()
        for tool in tools:
            self._mcp_tool_map[tool["name"]] = client.server_name
            self._mcp_tool_defs.append(tool)
        logger.info(
            "registry_register",
            extra={"tier": 2, "capability": client.server_name, "tools": len(tools)},
        )

    # ── Tier 3 ────────────────────────────────────────────────────────────────

    async def register_adapter(self, adapter: "OSSAdapter") -> None:
        self._adapters[adapter.name] = adapter
        logger.info("registry_register", extra={"tier": 3, "capability": adapter.name})

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

        tools.extend(self._mcp_tool_defs)

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

        Runs an isolated agentic loop using the same AnthropicClient and registry tools
        as the parent. The Task tool is excluded from the sub-agent's tool list to
        prevent recursive spawning. Safety guard fires at _SUB_AGENT_MAX_ITERATIONS.
        """
        if self._client is None:
            logger.error("task_tool_no_client", extra={"request_id": context.request_id})
            return "Task sub-agent unavailable: AnthropicClient was not injected into the registry."

        description = args.get("description", "")
        prompt = args.get("prompt", "")

        # Sub-agents run on a cheaper model with a SEPARATE rate-limit pool
        # (Haiku by default), so their burst of tool-loop calls doesn't stack
        # against the main loop's per-minute token limit. Falls back to the
        # parent model if sub_agent_model is unset.
        from app.config import settings
        sub_model = settings.sub_agent_model or context.model

        logger.info(
            "task_sub_agent_start",
            extra={
                "request_id": context.request_id,
                "description": description,
                "model": sub_model,
            },
        )

        # Sub-agents get all tools except Task (prevent infinite recursion)
        tools = [t for t in self.list_tools() if t["name"] != "Task"]

        messages: list[dict] = [{"role": "user", "content": prompt}]
        system = (
            "You are a focused sub-agent. Complete the following specific task and return "
            "your findings in full. Do not ask for clarification — act on what you have.\n\n"
            f"Task: {description}"
        )

        iterations = 0

        while True:
            if iterations >= _SUB_AGENT_MAX_ITERATIONS:
                logger.error(
                    "sub_agent_safety_guard",
                    extra={
                        "request_id": context.request_id,
                        "iterations": iterations,
                        "description": description,
                    },
                )
                return (
                    f"Sub-agent safety guard triggered after {iterations} tool iterations. "
                    "Task may be incomplete."
                )

            response = await self._client.create_message(
                model=sub_model,
                system=system,
                messages=messages,
                tools=tools,
                max_tokens=8096,
            )

            stop_reason = response.stop_reason
            messages.append({"role": "assistant", "content": _blocks_to_dicts(response.content)})

            if stop_reason == "end_turn":
                text_parts = [
                    b.text for b in response.content if hasattr(b, "text") and b.text
                ]
                result = "\n".join(text_parts) or "(sub-agent returned no text)"
                logger.info(
                    "task_sub_agent_done",
                    extra={
                        "request_id": context.request_id,
                        "iterations": iterations,
                        "result_length": len(result),
                    },
                )
                return result

            if stop_reason == "tool_use":
                iterations += 1
                tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
                
                # Log all tool calls first
                for block in tool_use_blocks:
                    logger.info(
                        "sub_agent_tool_call",
                        extra={
                            "request_id": context.request_id,
                            "tool": block.name,
                            "tool_id": block.id,
                        },
                    )
                    
                # Execute all tools in parallel
                exec_tasks = [
                    self.execute(block.name, block.input, context)
                    for block in tool_use_blocks
                ]
                results = await asyncio.gather(*exec_tasks)
                
                # Format results and zip them back
                tool_results = [
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": res,
                    }
                    for block, res in zip(tool_use_blocks, results)
                ]
                
                messages.append({"role": "user", "content": tool_results})

            elif stop_reason in ("max_tokens", "pause_turn"):
                messages.append(
                    {"role": "user", "content": [{"type": "text", "text": "Continue."}]}
                )

            else:
                logger.warning(
                    "sub_agent_unknown_stop",
                    extra={"request_id": context.request_id, "stop_reason": stop_reason},
                )
                return f"Sub-agent stopped unexpectedly (reason: {stop_reason})."

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