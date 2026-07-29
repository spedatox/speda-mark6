import logging
from typing import TYPE_CHECKING

from app.legion.roster import TASK_TOOL_DEFINITION

if TYPE_CHECKING:
    from app.adapters.base import OSSAdapter
    from app.core.context import AgentContext
    from app.mcp.client import MCPClient
    from app.profiles.registry import ProfileRegistry
    from app.services.llm_client import LLMClient
    from app.skills.base import Skill

logger = logging.getLogger(__name__)

# Runtime-infrastructure skills every agent gets regardless of its tool
# allowlist: memory, the progressive-disclosure loader, and the lazy-load
# meta-tool are part of the engine, not domain capabilities. A scoped agent
# still needs them to function.
_ALWAYS_AVAILABLE: frozenset = frozenset(
    {"memory", "read_skill", "use_toolset", "tool_search"}
)


class CapabilityRegistry:
    """
    Single plug-in interface for all four capability tiers.
    AgentOrchestrator calls list_tools() — it never hardcodes tool definitions.
    Adding a capability = register it here. The orchestrator never changes.

    Startup registration order (non-negotiable):
      Tier 0 (Task) → Tier 1 (Skills) → Tier 2 (MCP) → Tier 3 (Adapters)
    """

    def __init__(
        self,
        client: "LLMClient | None" = None,
        profiles: "ProfileRegistry | None" = None,
    ) -> None:
        self._client = client            # Injected at startup — required for the Legion
        self._profiles = profiles        # Drives provider-agnostic worker model resolution
        self._legion = None              # LegionRunner, built in register_legion()
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

    # ── Tier 0 — The Legion ───────────────────────────────────────────────────

    def register_legion(self) -> None:
        """Register The Legion (wire name "Task"). Must be called FIRST.

        Always registered, but hidden at runtime when budget mode is on (see
        list_tools) — so budget mode can be toggled live without a restart."""
        from app.legion.runner import LegionRunner

        self._legion = LegionRunner(self._client, self, self._profiles)
        self._task_tool_registered = True
        logger.info(
            "registry_register",
            extra={"tier": 0, "capability": "Task", "brand": "legion"},
        )

    async def legion_shutdown(self) -> None:
        """Cancel in-flight background legionnaires (lifespan teardown)."""
        if self._legion is not None:
            await self._legion.shutdown()

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
        loaded_tools: set[str] | None = None,
        defer_loading: bool = False,
    ) -> list[dict]:
        """
        Return tools across all tiers in Anthropic tool format.

        With lazy_tools on, MCP tools are included only for servers that are
        always-on or have been loaded this turn (active_servers) — keeping the
        cached prefix small. The rest are advertised via toolset_catalog() and
        pulled in on demand by the use_toolset tool.

        offline_only (Dead Zone Protocol): only Tier-1 skills that work without
        an uplink survive — MCP servers, adapters and the Legion (whose workers
        spawn LLM calls of their own) are all dropped.

        allowlist (per-agent scoping): when set, only tools whose owning
        capability (skill name / MCP server / adapter / "Task") is listed are
        returned — runtime-infrastructure skills (_ALWAYS_AVAILABLE) always pass.
        None = the full registry (e.g. SPEDA the orchestrator). The profile
        declares the allowlist; the registry enforces it (Rules 5 + 10).

        loaded_tools: deferred tools resolved by `tool_search` this turn. They
        are APPENDED after the core set, never spliced into it, so the cached
        prefix survives a mid-turn resolution.

        defer_loading: the provider understands Anthropic's `defer_loading` flag,
        so deferred tools ride along flagged instead of being withheld.
        """
        from app.config import settings
        from app.core.runtime_state import get_budget_mode, get_disabled_servers

        active = set(active_servers or set()) | self._always_on()
        disabled = get_disabled_servers()
        loaded = set(loaded_tools or set())

        tools: list[dict] = []

        if self._task_tool_registered and not get_budget_mode() and not offline_only:
            tools.append(TASK_TOOL_DEFINITION)

        for skill in self._skills.values():
            if offline_only and getattr(skill, "requires_network", False):
                continue
            if not self._agent_may_use(skill, agent_id):
                continue  # privileged skill, wrong agent (e.g. system_ops → orion only)
            defn = skill.to_tool_definition()
            if (
                settings.lazy_tools
                and not offline_only   # dead zone already filters to a tiny set;
                                       # don't also hide it behind a search
                and getattr(skill, "deferred", False)
                and skill.name not in _ALWAYS_AVAILABLE
                and skill.name not in loaded
            ):
                if defer_loading:
                    # Anthropic: ship the definition flagged, and let the API's
                    # own tool-search server tool resolve it. The flag is what
                    # keeps it out of the model's context until then.
                    defn = {**defn, "defer_loading": True}
                else:
                    continue  # resolved by the tool_search skill instead
            tools.append(defn)

        if offline_only:
            return self._apply_allowlist(tools, allowlist)

        for tool in self._mcp_tool_defs:
            srv = self._mcp_tool_map.get(tool["name"])
            if srv in disabled:
                continue
            if settings.lazy_tools and srv not in active and tool["name"] not in loaded:
                if defer_loading:
                    tools.append({**tool, "defer_loading": True})
                continue  # otherwise resolved by tool_search
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

    def deferred_tools(
        self, allowlist: set[str] | None = None, active_servers: set[str] | None = None,
        agent_id: str | None = None,
    ) -> list[dict]:
        """Every tool this agent MAY use that is not in its prompt prefix.

        The single source of what `tool_search` can resolve and what the name
        index advertises — both read this, so the catalog can never drift from
        what a search will actually find (Rule 5: the registry is the only thing
        that knows what tools exist).
        """
        from app.config import settings
        from app.core.runtime_state import get_disabled_servers

        if not settings.lazy_tools:
            return []
        active = set(active_servers or set()) | self._always_on()
        disabled = get_disabled_servers()

        out: list[dict] = []
        for skill in self._skills.values():
            if not getattr(skill, "deferred", False):
                continue
            if skill.name in _ALWAYS_AVAILABLE:
                continue
            if not self._agent_may_use(skill, agent_id):
                continue
            if allowlist is not None and not self._tool_in_allowlist(skill.name, allowlist):
                continue
            defn = skill.to_tool_definition()
            # Search-only field, stripped before the definition ever reaches a
            # provider (underscore marker, same convention as `_cache`).
            keywords = getattr(skill, "search_keywords", "")
            if keywords:
                defn = {**defn, "_keywords": keywords}
            out.append(defn)

        for tool in self._mcp_tool_defs:
            srv = self._mcp_tool_map.get(tool["name"])
            if srv in disabled or srv in active:
                continue
            if allowlist is not None and srv not in allowlist:
                continue
            out.append(tool)
        return out

    def tool_index(
        self, allowlist: set[str] | None = None, active_servers: set[str] | None = None,
        agent_id: str | None = None, loaded_tools: set[str] | None = None,
    ) -> str:
        """The system-prompt block listing deferred tools BY NAME ONLY.

        Names cost a few tokens each where full Rule 11 descriptions cost
        hundreds, so the model can still see its whole capability surface for a
        fraction of the prefix. It learns what each one does by searching for it.
        Mirrors how Anthropic surfaces deferred tools: name now, schema on demand.
        """
        loaded = set(loaded_tools or set())
        names = sorted(
            t["name"] for t in self.deferred_tools(allowlist, active_servers, agent_id)
            if t["name"] not in loaded
        )
        if not names:
            return ""
        return (
            "## Additional tools (not yet loaded)\n\n"
            "These tools exist but their full descriptions are NOT loaded, to keep "
            "this prompt small. You cannot call one until you load it: call "
            "`tool_search` with a few words describing what you need (or the exact "
            "tool name), read the schemas it returns, then call the tool. Search "
            "once for everything the task needs rather than repeatedly — a search "
            "costs a round trip. Tools already listed in your tools array need no "
            "search.\n\n"
            + ", ".join(f"`{n}`" for n in names)
        )

    def search_tools(
        self, query: str, allowlist: set[str] | None = None,
        active_servers: set[str] | None = None, agent_id: str | None = None,
        limit: int = 8,
    ) -> list[dict]:
        """Rank deferred tools against a free-text query.

        Scored rather than regex-matched: the model describes what it wants in
        its own words ("send the owner a message", "route to the airport"), and
        an exact-name match still wins outright so `select:`-style precise
        lookups behave. Name hits outrank description hits because a tool named
        for the thing you asked about is almost always the one you meant.
        """
        import re

        terms = [t for t in re.split(r"[^a-z0-9]+", query.lower()) if len(t) > 2]
        candidates = self.deferred_tools(allowlist, active_servers, agent_id)
        if not terms:
            return []

        scored: list[tuple[int, str, dict]] = []
        for tool in candidates:
            name = tool["name"].lower()
            desc = (tool.get("description") or "").lower()
            keywords = (tool.get("_keywords") or "").lower()
            score = 0
            if name == query.strip().lower():
                score += 1000  # exact name — the model knew what it wanted
            for term in terms:
                if term in name:
                    score += 10
                if term in keywords:
                    score += 8   # author-supplied synonym: nearly as strong as
                                 # the name, and the whole point of the field
                if term in desc:
                    score += 1
            if score:
                scored.append((score, tool["name"], tool))
        if not scored:
            return []
        scored.sort(key=lambda r: (-r[0], r[1]))

        # Relative cutoff, not just a top-N slice. Rule 11 descriptions are long
        # and prose-heavy, so a one-word incidental hit ("check", "search")
        # scores on tools that have nothing to do with the query — returning
        # them buys nothing and pours whole schemas into the context, which is
        # the cost this mechanism exists to avoid. Anything less than half the
        # best match is noise; an exact name match therefore returns essentially
        # itself.
        best = scored[0][0]
        floor = max(3, best // 2)
        return [t for s, _, t in scored[:limit] if s >= floor]

    def toolset_catalog(
        self, allowlist: set[str] | None = None, active: set[str] | None = None
    ) -> str:
        """Compact catalog of NOT-yet-loaded MCP toolsets, addressed by SERVER.

        Superseded for the system prompt by `tool_index` + `tool_search`, which
        work per tool and cover Tier 1 as well. This stays because loading a
        whole server in one call is still the right move when a task obviously
        needs all of Gmail or all of Calendar, and `use_toolset` remains
        registered for exactly that. Skill groups are gone — a Tier-1 skill is
        deferred or not, and `tool_search` resolves it individually.
        """
        from app.config import settings
        from app.core.runtime_state import get_disabled_servers

        if not settings.lazy_tools:
            return ""
        always_on = self._always_on()
        disabled = get_disabled_servers()
        loaded = set(active or set())

        by_server: dict[str, list[str]] = {}
        for tool in self._mcp_tool_defs:
            srv = self._mcp_tool_map.get(tool["name"], "?")
            by_server.setdefault(srv, []).append(tool["name"])

        lines = []
        for srv in sorted(by_server):
            if srv in always_on or srv in disabled or srv in loaded:
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
            "task needs one, call `use_toolset` with the toolset name to load it, "
            "THEN use its tools (they aren't callable until loaded). Load only what "
            "the task needs — you can load several in one turn, and a toolset stays "
            "loaded for the rest of the conversation.\n\n" + "\n".join(lines)
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
        """Route a tool call to the correct tier handler.

        Identical READ-ONLY calls are answered from a per-turn memo. Models
        re-ask for the same thing inside one turn more often than you'd guess —
        a single observed health briefing called `health_data` nine times with
        four exact-duplicate argument sets — and every one of those duplicates
        costs a full round trip of the whole prompt prefix, not just the tool's
        own latency. The memo is deliberately narrow: read-only skills only
        (Rule 9's annotation is what makes replay safe), scoped to one turn, and
        dropped wholesale the moment any mutating tool runs, because a write can
        change what a subsequent read should return.
        """
        # `extra` is absent on the light stand-in contexts some callers build,
        # so the memo degrades to off rather than making tool execution depend
        # on a field that isn't part of the contract.
        extra = getattr(context, "extra", None)
        memo_key: tuple[str, str] | None = None
        if extra is not None and self._memoizable(tool_name):
            try:
                import json as _json

                memo = extra.setdefault("tool_memo", {})
                memo_key = (tool_name, _json.dumps(args, sort_keys=True, default=str))
                if memo_key in memo:
                    logger.info(
                        "tool_memo_hit",
                        extra={"tool": tool_name, "request_id": context.request_id},
                    )
                    return memo[memo_key]
            except (TypeError, ValueError):
                memo_key = None  # unserialisable args — just run it
        elif extra is not None and tool_name != "Task":
            # A mutating call invalidates every cached read. Task is exempt: a
            # legionnaire runs in its own context and cannot mutate this one's.
            extra.pop("tool_memo", None)

        result = await self._dispatch(tool_name, args, context)
        # Never memoize a failure: a transient error must not be replayed as
        # this tool's answer for the rest of the turn.
        if memo_key is not None and extra is not None and not result.startswith("Error"):
            extra.setdefault("tool_memo", {})[memo_key] = result
        return result

    def _memoizable(self, tool_name: str) -> bool:
        """Whether an identical repeat call may be served from the per-turn memo.
        Read-only Tier-1 skills only — those carry the Rule 9 annotation that
        promises no side effects. MCP tools and adapters are excluded: nothing
        in their definitions tells us whether a repeat is safe, and guessing
        wrong on a write is far worse than paying for a duplicate read."""
        skill = self._skills.get(tool_name)
        return bool(skill is not None and getattr(skill, "read_only", False))

    async def _dispatch(self, tool_name: str, args: dict, context: "AgentContext") -> str:
        """Tier routing proper. execute() wraps this with the per-turn memo."""
        try:
            if tool_name == "Task":
                if self._legion is None:
                    return "The Legion is not registered on this deployment."
                return await self._legion.run_worker(args, context)

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

    # ── Ownership lookups (used by the Legion's tool scoping) ─────────────────

    def tool_owner(self, tool_name: str) -> tuple[str, str]:
        """Which capability owns a tool: ("skill"|"mcp"|"adapter"|"task"|"unknown",
        owner name). The Legion uses this to scope read-only workers to
        read-only skills + research MCP servers."""
        if tool_name == "Task":
            return ("task", "Task")
        if tool_name in self._skills:
            return ("skill", tool_name)
        srv = self._mcp_tool_map.get(tool_name)
        if srv is not None:
            return ("mcp", srv)
        if tool_name in self._adapters:
            return ("adapter", tool_name)
        return ("unknown", tool_name)

    def skill_is_read_only(self, skill_name: str) -> bool:
        skill = self._skills.get(skill_name)
        return bool(skill is not None and getattr(skill, "read_only", False))

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