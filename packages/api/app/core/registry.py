import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.adapters.base import OSSAdapter
    from app.core.context import AgentContext
    from app.mcp.client import MCPClient
    from app.services.llm_client import LLMClient
    from app.skills.base import Skill

logger = logging.getLogger(__name__)

_SUB_AGENT_MAX_ITERATIONS = 15  # Sub-agents are focused tasks; lower than the main loop's 30

# Runtime-infrastructure skills every agent gets regardless of its tool
# allowlist: memory, the progressive-disclosure loader, and the lazy-load
# meta-tool are part of the engine, not domain capabilities. A scoped agent
# still needs them to function.
_ALWAYS_AVAILABLE: frozenset = frozenset({"memory", "read_skill", "use_toolset"})

# The Task tool definition (Tier 0 — Anthropic Agent SDK built-in).
# Registered first at startup, before all other tiers.
_TASK_TOOL_DEFINITION: dict = {
    "name": "Task",
    "description": (
        "Spawns an isolated, billed sub-agent for a heavy research task. This is "
        "EXPENSIVE and RARE — avoid it unless clearly necessary. "
        "Spawn ONLY when the user explicitly asked for a deep/thorough research "
        "report AND it genuinely needs 6+ independent searches across distinct "
        "subtopics. "
        "Do NOT use it for news, current events, 'what's happening' queries, quick "
        "facts, lookups, writes, reminders, or anything completable with a handful "
        "of direct tool calls — handle those yourself in the main loop by calling "
        "search tools directly. Running several searches yourself is preferred over "
        "spawning a sub-agent. When in doubt, do NOT spawn. "
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

    def __init__(self, client: "LLMClient | None" = None) -> None:
        self._client = client            # Injected at startup — required for Task sub-agents
        self._task_tool_registered = False
        self._skills: dict[str, "Skill"] = {}
        self._mcp_clients: dict[str, "MCPClient"] = {}
        self._mcp_tool_map: dict[str, str] = {}  # tool_name → server_name
        self._mcp_tool_defs: list[dict] = []     # full definitions for list_tools()
        self._adapters: dict[str, "OSSAdapter"] = {}
        # Dead Zone Protocol — cached connectivity probe (registry lives on
        # app.state, so this is instance state, not a module global).
        self._dz_checked_at: float = 0.0
        self._dz_offline: bool = False

    # ── Dead Zone Protocol ─────────────────────────────────────────────────────

    async def dead_zone_active(self) -> bool:
        """
        True when SPEDA is operating without an uplink. DEAD_ZONE_MODE=on forces
        it (dev testing), =off disables it, =auto (default) probes connectivity
        and caches the verdict for 60s. In the dead zone, list_tools() filters
        to offline-capable Tier-1 skills only — a local model calling web search
        with no internet just burns its own context on guaranteed failures.
        """
        import time

        from app.config import settings

        mode = settings.dead_zone_mode.strip().lower()
        if mode == "on":
            return True
        if mode == "off":
            return False

        now = time.monotonic()
        if self._dz_checked_at and now - self._dz_checked_at < 60:
            return self._dz_offline

        import httpx

        was_offline = self._dz_offline
        try:
            # Any HTTP response at all (even 4xx) proves the uplink is alive.
            async with httpx.AsyncClient(timeout=2.0) as probe:
                await probe.head("https://api.anthropic.com")
            self._dz_offline = False
        except Exception:
            self._dz_offline = True
        self._dz_checked_at = now
        if self._dz_offline != was_offline:
            logger.warning(
                "dead_zone_engaged" if self._dz_offline else "dead_zone_lifted",
                extra={"mode": mode},
            )
        return self._dz_offline

    # ── Tier 0 ────────────────────────────────────────────────────────────────

    def register_task_tool(self) -> None:
        """Register the SDK built-in Task tool. Must be called FIRST.

        Always registered, but hidden at runtime when budget mode is on (see
        list_tools) — so budget mode can be toggled live without a restart."""
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

    async def reconnect_mcp_servers(self, clients: list["MCPClient"]) -> int:
        """Live (re)connect a set of MCP clients — drop any existing same-named
        server's tools first, then connect + register the new ones. Used by the
        in-app 'Sign in with Google' flow so services appear without a restart.
        Returns the number successfully connected."""
        connected = 0
        for client in clients:
            name = client.server_name
            # Drop the old instance's tools/mapping if present.
            old = self._mcp_clients.pop(name, None)
            if old is not None:
                try:
                    await old.disconnect()
                except BaseException:
                    pass
            self._mcp_tool_defs = [
                t for t in self._mcp_tool_defs if self._mcp_tool_map.get(t["name"]) != name
            ]
            self._mcp_tool_map = {k: v for k, v in self._mcp_tool_map.items() if v != name}
            await self.register_mcp(client)
            if name in self._mcp_clients:
                connected += 1
        return connected

    # ── Tier 3 ────────────────────────────────────────────────────────────────

    async def register_adapter(self, adapter: "OSSAdapter") -> None:
        self._adapters[adapter.name] = adapter
        logger.info("registry_register", extra={"tier": 3, "capability": adapter.name})

    # ── Unified interface ──────────────────────────────────────────────────────

    def _always_on(self) -> set[str]:
        from app.config import settings
        return {s.strip() for s in settings.always_on_servers.split(",") if s.strip()}

    def list_tools(
        self,
        active_servers: set[str] | None = None,
        offline_only: bool = False,
        allowlist: set[str] | None = None,
        agent_id: str | None = None,
    ) -> list[dict]:
        """
        Return tools across all tiers in Anthropic tool format.

        With lazy_tools on, MCP tools are included only for servers that are
        always-on or have been loaded this turn (active_servers) — keeping the
        cached prefix small. The rest are advertised via toolset_catalog() and
        pulled in on demand by the use_toolset tool.

        offline_only (Dead Zone Protocol): only Tier-1 skills that work without
        an uplink survive — MCP servers, adapters and Task sub-agents (which
        spawn LLM calls of their own) are all dropped.

        allowlist (per-agent scoping): when set, only tools whose owning
        capability (skill name / MCP server / adapter / "Task") is listed are
        returned — runtime-infrastructure skills (_ALWAYS_AVAILABLE) always pass.
        None = the full registry (e.g. SPEDA the orchestrator). The profile
        declares the allowlist; the registry enforces it (Rules 5 + 10).
        """
        from app.config import settings
        from app.core.runtime_state import get_budget_mode, get_disabled_servers

        active = set(active_servers or set()) | self._always_on()
        disabled = get_disabled_servers()

        tools: list[dict] = []

        if self._task_tool_registered and not get_budget_mode() and not offline_only:
            tools.append(_TASK_TOOL_DEFINITION)

        for skill in self._skills.values():
            if offline_only and getattr(skill, "requires_network", False):
                continue
            if not self._agent_may_use(skill, agent_id):
                continue  # privileged skill, wrong agent (e.g. system_ops → orion only)
            tools.append(skill.to_tool_definition())

        if offline_only:
            return self._apply_allowlist(tools, allowlist)

        for tool in self._mcp_tool_defs:
            srv = self._mcp_tool_map.get(tool["name"])
            if srv in disabled:
                continue
            if settings.lazy_tools and srv not in active:
                continue  # not loaded yet — advertised in the catalog instead
            tools.append(tool)

        for adapter in self._adapters.values():
            tools.append(adapter.to_tool_definition())

        return self._apply_allowlist(tools, allowlist)

    @staticmethod
    def _agent_may_use(skill: "Skill", agent_id: str | None) -> bool:
        """A skill with `restricted_to` set is visible/callable ONLY to those
        agents. None agent_id (unscoped callers) never sees a restricted skill —
        privilege is opt-in by agent, never by omission."""
        restricted = getattr(skill, "restricted_to", None)
        if restricted is None:
            return True
        return agent_id is not None and agent_id in restricted

    def _apply_allowlist(self, tools: list[dict], allowlist: set[str] | None) -> list[dict]:
        if allowlist is None:
            return tools
        return [t for t in tools if self._tool_in_allowlist(t["name"], allowlist)]

    def _tool_in_allowlist(self, tool_name: str, allowed: set[str]) -> bool:
        """A tool is permitted if it is runtime infrastructure, or its owning
        capability (skill name / MCP server / adapter / 'Task') is in the agent's
        declared allowlist."""
        if tool_name in _ALWAYS_AVAILABLE:
            return True
        if tool_name == "Task":
            return "Task" in allowed
        if tool_name in self._skills:
            return tool_name in allowed
        srv = self._mcp_tool_map.get(tool_name)
        if srv is not None:
            return srv in allowed
        if tool_name in self._adapters:
            return tool_name in allowed
        return tool_name in allowed

    def toolset_catalog(self, allowlist: set[str] | None = None) -> str:
        """Compact catalog of NOT-yet-loaded MCP toolsets for the system prompt,
        so an agent knows what it can pull in via use_toolset. When an allowlist
        is given, only servers the agent may use are advertised."""
        from app.config import settings
        from app.core.runtime_state import get_disabled_servers

        if not settings.lazy_tools:
            return ""
        always_on = self._always_on()
        disabled = get_disabled_servers()

        by_server: dict[str, list[str]] = {}
        for tool in self._mcp_tool_defs:
            srv = self._mcp_tool_map.get(tool["name"], "?")
            by_server.setdefault(srv, []).append(tool["name"])

        lines = []
        for srv in sorted(by_server):
            if srv in always_on or srv in disabled:
                continue
            if allowlist is not None and srv not in allowlist:
                continue  # agent isn't scoped for this server
            names = by_server[srv]
            sample = ", ".join(n.replace("_", " ") for n in names[:6])
            more = f", +{len(names) - 6} more" if len(names) > 6 else ""
            lines.append(f"- `{srv}` ({len(names)} tools): {sample}{more}")
        if not lines:
            return ""
        return (
            "## Loadable toolsets\n\n"
            "To stay fast and cheap, most tools are NOT loaded by default. When a "
            "task needs one, call `use_toolset` with the server name to load it, "
            "THEN use its tools (they aren't callable until loaded). Load only what "
            "the task needs.\n\n" + "\n".join(lines)
        )

    def server_summary(self) -> list[dict]:
        """Per-MCP-server status for the Connections UI: name, connected, tool
        count, active (not user-disabled), and a rough token estimate."""
        from app.core.runtime_state import get_disabled_servers

        disabled = get_disabled_servers()
        # Rough token estimate per tool definition (chars/4).
        def est(tool: dict) -> int:
            import json
            return len(json.dumps(tool)) // 4

        by_server: dict[str, dict] = {}
        for tool in self._mcp_tool_defs:
            srv = self._mcp_tool_map.get(tool["name"], "unknown")
            s = by_server.setdefault(srv, {"server": srv, "tools": 0, "tokens": 0})
            s["tools"] += 1
            s["tokens"] += est(tool)
        for srv, client in self._mcp_clients.items():
            s = by_server.setdefault(srv, {"server": srv, "tools": 0, "tokens": 0})
            s["connected"] = getattr(client, "_connected", False)
        always_on = self._always_on()
        out = []
        for srv, s in by_server.items():
            s.setdefault("connected", False)
            s["active"] = srv not in disabled
            s["always_on"] = srv in always_on  # in prefix without use_toolset
            out.append(s)
        return sorted(out, key=lambda x: x["server"])

    async def execute(self, tool_name: str, args: dict, context: "AgentContext") -> str:
        """Route a tool call to the correct tier handler."""
        try:
            if tool_name == "Task":
                return await self._execute_task(args, context)

            if tool_name in self._skills:
                skill = self._skills[tool_name]
                if not self._agent_may_use(skill, context.agent_id):
                    logger.warning(
                        "restricted_tool_blocked",
                        extra={
                            "tool": tool_name,
                            "agent_id": context.agent_id,
                            "request_id": context.request_id,
                        },
                    )
                    return (
                        f"Error: the tool '{tool_name}' is restricted and not available "
                        f"to agent '{context.agent_id}'. Do not call it again."
                    )
                return await skill.execute(args, context)

            if tool_name in self._mcp_tool_map:
                server_name = self._mcp_tool_map[tool_name]
                return await self._mcp_clients[server_name].call_tool(tool_name, args)

            if tool_name in self._adapters:
                return await self._adapters[tool_name].execute(args, context)

            # Hallucinated tool name (open-weight models do this). Return a
            # corrective result instead of a bare error so the model can
            # self-recover in the next iteration rather than looping or dying.
            known = sorted(
                ({"Task"} if self._task_tool_registered else set())
                | set(self._skills)
                | set(self._mcp_tool_map)
                | set(self._adapters)
            )
            logger.warning(
                "unknown_tool_called",
                extra={"tool": tool_name, "request_id": context.request_id},
            )
            return (
                f"Error: the tool '{tool_name}' does not exist — do not call it "
                f"again. Tools that actually exist: {', '.join(known)}. If none "
                "of them fits, answer directly from your own knowledge instead "
                "of calling a tool."
            )

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

        Runs an isolated agentic loop using the same LLMClient and registry tools
        as the parent. The Task tool is excluded from the sub-agent's tool list to
        prevent recursive spawning. Safety guard fires at _SUB_AGENT_MAX_ITERATIONS.
        """
        if self._client is None:
            logger.error("task_tool_no_client", extra={"request_id": context.request_id})
            return "Task sub-agent unavailable: LLMClient was not injected into the registry."

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

        # Sub-agents get all tools except Task (prevent infinite recursion),
        # scoped to the spawning agent's allowlist (inherited via the context).
        tools = [
            t for t in self.list_tools(
                allowlist=context.extra.get("tool_allowlist"), agent_id=context.agent_id
            )
            if t["name"] != "Task"
        ]

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