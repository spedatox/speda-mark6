import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.adapters.base import OSSAdapter
    from app.core.context import AgentContext
    from app.mcp.client import MCPClient
    from app.skills.base import Skill

logger = logging.getLogger(__name__)

# The Task tool definition (Tier 0 — bounded in-process worker).
# Registered first at startup, before all other tiers.
_TASK_TOOL_DEFINITION: dict = {
    "name": "Task",
    "description": (
        "Runs an isolated read-only worker on a focused task with inherited model and thinking policy. "
        "Use this when a task requires 3+ independent information sources, deep multi-source research, "
        "or when intermediate results would significantly bloat this context window. "
        "Do not use this for lookups, writes, reminders, or tasks completable in 1–3 tool calls. "
        "Choose scout, researcher, analyst, judge, archivist or general for isolated read-only Legion work. "
        "Autobot, Decepticon and Forge reviewer roles require this conversation's bound native project and use its execution queue. "
        "Read-only work returns findings by default; background or run_in_background returns an admission to collect with legion_inspect or legion_status. "
        "All children share the admitted root budget and deadline, and workers cannot spawn more workers."
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
            "thinking": {
                "type": "object",
                "properties": {"mode": {"enum": ["provider_default", "off", "enabled", "effort", "budget", "inherit"]},
                               "value": {"type": ["string", "integer", "null"]}},
                "required": ["mode"], "additionalProperties": False,
            },
            "worker_id": {"type":"string","enum":["scout","researcher","analyst","judge","archivist","general","autobot","decepticon","forge_reviewer"]},
            "subagent_type": {"type":"string"},
            "background": {"type":"boolean","description":"Detach read-only work into a tracked child; result must be collected. Forge work always uses the bound project queue."},
            "run_in_background": {"type":"boolean","description":"Historical alias for background; conflicting values are refused."},
            "acceptance": {"type":"array","items":{"type":"string","maxLength":1000},"minItems":1,"maxItems":20},
            "dependencies": {"type":"array","items":{"type":"string"},"maxItems":20},
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

    def __init__(self, receipt_sessions=None) -> None:
        self._task_tool_registered = False
        self._receipt_sessions = receipt_sessions
        self._task_runner = None
        self._skills: dict[str, "Skill"] = {}
        self._mcp_clients: dict[str, "MCPClient"] = {}
        self._mcp_incarnations = {}
        self._mcp_tool_map: dict[str, str] = {}  # tool_name → server_name
        self._mcp_definitions = {}
        self._mcp_original_names = {}
        self._adapters: dict[str, "OSSAdapter"] = {}

    # ── Tier 0 ────────────────────────────────────────────────────────────────

    def register_task_tool(self) -> None:
        """Register the SDK built-in Task tool. Must be called FIRST."""
        self._task_tool_registered = True
        logger.info("registry_register", extra={"tier": 0, "capability": "Task"})

    # ── Tier 1 ────────────────────────────────────────────────────────────────

    async def register_skill(self, skill: "Skill") -> None:
        if skill.name in self._skills or skill.name in self._mcp_tool_map or skill.name == "Task":
            raise ValueError("Capability identity already registered")
        self._skills[skill.name] = skill
        logger.info("registry_register", extra={"tier": 1, "capability": skill.name})

    # ── Tier 2 ────────────────────────────────────────────────────────────────

    async def register_mcp(self, client: "MCPClient") -> None:
        if client.server_name in self._mcp_clients:
            raise ValueError("MCP server identity already registered")
        await client.connect()
        self._mcp_clients[client.server_name] = client
        from uuid import uuid4
        self._mcp_incarnations[client.server_name] = uuid4().hex
        self._refresh_mcp(client)
        client.on_catalog_changed = self._refresh_mcp
        logger.info(
            "registry_register",
            extra={"tier": 2, "capability": client.server_name, "tools": len(client._tools)},
        )

    def _refresh_mcp(self, client):
        if self._mcp_clients.get(client.server_name) is not client:
            return  # An old connection cannot republish after replacement/revocation.
        import hashlib
        import re
        for name in [n for n, s in self._mcp_tool_map.items() if s == client.server_name]:
            del self._mcp_tool_map[name]
            self._mcp_definitions.pop(name, None)
            self._mcp_original_names.pop(name, None)
        for definition in client._tools:
            original = definition['name']
            slug = re.sub(r'[^a-zA-Z0-9_]', '_', original)[:20]
            suffix = hashlib.sha256(f'{client.server_name}:{original}'.encode()).hexdigest()[:10]
            name = f'mcp_{client.server_name[:24]}_{slug}_{suffix}'
            if name in self._mcp_tool_map or name in self._skills:
                raise ValueError('MCP capability identity collision')
            self._mcp_tool_map[name] = client.server_name
            self._mcp_original_names[name] = original
            self._mcp_definitions[name] = definition

    def _mcp_allowed(self, name, context):
        client = self._mcp_clients[self._mcp_tool_map[name]]
        if not client._connected:
            return False
        if not getattr(client, 'active', True):
            return False
        if context is None:
            return True
        if context.agent_id not in client.allowed_agents or context.extra.get('offline_only'):
            return False
        users = getattr(client, 'allowed_users', None)
        if users is not None and getattr(context, 'user_id', None) not in users:
            return False
        active = context.extra.get('active_servers')
        if active is not None and client.server_name not in active:
            return False
        if context.task_role == 'worker' and not client.read_only(self._mcp_original_names[name]):
            return False
        # Pin grants on first discovery for the run; a new connection cannot
        # silently expand an already-running turn's authority.
        grants = context.extra.setdefault('mcp_grants', frozenset(s for s, c in self._mcp_clients.items()
            if context.agent_id in c.allowed_agents and c._connected and getattr(c, 'active', True)))
        bindings = context.extra.setdefault('mcp_bindings', dict(self._mcp_incarnations))
        return (client.server_name in grants and
                bindings.get(client.server_name) == self._mcp_incarnations.get(client.server_name))

    async def drop_mcp_server(self, name):
        """Revoke discovery/execution before waiting for the transport to close."""
        client = self._mcp_clients.pop(name, None)
        self._mcp_incarnations.pop(name, None)
        for tool in [n for n, server in self._mcp_tool_map.items() if server == name]:
            self._mcp_tool_map.pop(tool, None)
            self._mcp_definitions.pop(tool, None)
            self._mcp_original_names.pop(tool, None)
        if client:
            client.on_catalog_changed = None
            client.active = False
            await client.disconnect()

    # ── Tier 3 ────────────────────────────────────────────────────────────────

    async def register_adapter(self, adapter: "OSSAdapter") -> None:
        self._adapters[adapter.name] = adapter
        logger.info("registry_register", extra={"tier": 3, "capability": adapter.name})

    # ── Unified interface ──────────────────────────────────────────────────────

    def list_tools(self, context=None, *, include_deferred=False, for_execution=False) -> list[dict]:
        """
        Return all tools across all four tiers in Anthropic tool format.
        Claude sees no difference between tiers.
        """
        tools: list[dict] = []

        if self._task_tool_registered and not (context and context.task_role == "worker"):
            tools.append(_TASK_TOOL_DEFINITION)

        for skill in self._skills.values():
            # This owner-authorized telemetry tool grants no access to external tools or data.
            if skill.name == "report_incident":
                continue
            if context and context.task_role == "worker" and not skill.read_only:
                continue
            if context is not None and hasattr(skill, "available") and not skill.available(context):
                continue
            if context is not None and not for_execution and not skill.offered(context):
                continue
            if context is not None and skill.restricted_to is not None and context.agent_id not in skill.restricted_to:
                continue
            if context is not None and context.extra.get("offline_only") and skill.requires_network:
                continue
            if context is not None and skill.deferred and not include_deferred and skill.name not in context.extra.get("loaded_tools", set()):
                continue
            tools.append(skill.to_tool_definition())

        for name, server in self._mcp_tool_map.items():
            if not self._mcp_allowed(name, context):
                continue
            if context is not None and not include_deferred and name not in context.extra.get('loaded_tools', ()):
                continue
            definition = self._mcp_definitions[name]
            if context is not None:
                from app.mcp.client import fingerprint
                context.extra.setdefault('mcp_schemas', {}).setdefault(name, fingerprint(definition))
            tools.append({"name": name, "description": definition['description'], "input_schema": definition['input_schema']})

        if not (context and context.task_role == "worker"):
            for adapter in self._adapters.values():
                tools.append(adapter.to_tool_definition())

        if context is not None and context.allowed_tools is not None:
            tools = [tool for tool in tools if tool["name"] in context.allowed_tools or
                     (tool["name"] in self._mcp_tool_map and self._mcp_allowed(tool["name"], context))]
        if context is not None and context.effective_policy and context.effective_policy.snapshot.budget_mode:
            tools = [tool for tool in tools if tool['name'] not in {'Task', 'project_delegate'}]
        if context is not None and context.extra.get('worker_tool_scope') is not None:
            tools = [tool for tool in tools if tool['name'] in context.extra['worker_tool_scope']]
        if self._bounded_mail_automation(context):
            tools = [tool for tool in tools if self.call_is_read_only(tool['name'], {})
                     or (context.trigger_payload['mail_calendar_create'] and self._mail_calendar_tool(tool['name']))]
        if incident_tool := self._skills.get("report_incident"):
            tools.append(incident_tool.to_tool_definition())
        return tools

    def search_tools(self, query, context=None, *, agent_id=None, allowlist=None, active_servers=None):
        """Search only authorized definitions. Loading never grants authority."""
        import re
        if context is None:
            from types import SimpleNamespace
            context = SimpleNamespace(agent_id=agent_id or "speda", task_role="interactive",
                allowed_tools=allowlist, effective_policy=None, extra={"active_servers": active_servers})
        terms = set(re.findall(r"\w+", query.casefold()))
        ranked = []
        for definition in self.list_tools(context, include_deferred=True):
            name = definition["name"]
            skill = self._skills.get(name)
            original = self._mcp_original_names.get(name, name)
            identities = {name.casefold(), original.casefold()}
            exact = len(terms & identities)
            name_words = set(re.findall(r"[a-z0-9]+", f"{name} {original}".casefold()))
            name_matches = len(terms & name_words)
            words = f"{name} {original} {definition['description']} {getattr(skill, 'search_keywords', '')}".casefold()
            score = sum(term in words for term in terms)
            if score:
                ranked.append(((-exact, -name_matches, -score, name), definition))
        return [definition for _, definition in sorted(ranked, key=lambda item: item[0])[:5]]

    def call_is_read_only(self, name, args):
        skill = self._skills.get(name)
        if skill is not None:
            return skill.read_only
        server = self._mcp_tool_map.get(name)
        return bool(server and self._mcp_clients[server].read_only(self._mcp_original_names[name]))

    @staticmethod
    def _bounded_mail_automation(context):
        return (context is not None and context.task_role == 'background'
                and 'mail_calendar_create' in context.trigger_payload)

    def _mail_calendar_tool(self, name):
        return (self._mcp_tool_map.get(name) == 'google_calendar'
                and self._mcp_original_names.get(name) == 'calendar_create_event')

    @staticmethod
    def _calendar_instant(value):
        from datetime import datetime
        try:
            instant = datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return None
        return instant if instant.tzinfo else None

    def _check_mail_calendar_args(self, args, context):
        from datetime import datetime, timezone
        from app.schemas.llm import ProviderError
        if args.get('attendees') or args.get('recurrence') or args.get('calendar_id', 'primary') != 'primary':
            raise ProviderError('unauthorized_tool', 'Mail automations can create only one-off primary calendar entries without attendees')
        start = self._calendar_instant(args.get('start'))
        end = self._calendar_instant(args.get('end'))
        if not start or not end or end <= start or start <= datetime.now(timezone.utc):
            raise ProviderError('invalid_arguments', 'Use a future start and end time with explicit UTC offsets')
        if not any(lower <= start and end <= upper for lower, upper in context.extra.get('mail_calendar_checked', ())):
            raise ProviderError('unauthorized_tool', 'Check the primary calendar for this time range before creating the entry')

    def discovery_index(self, context):
        visible = {tool['name'] for tool in self.list_tools(context)}
        return [tool['name'] for tool in self.list_tools(context, include_deferred=True) if tool['name'] not in visible]

    def validate_call(self, call, context):
        from jsonschema import Draft202012Validator
        from app.schemas.llm import ProviderError
        if not getattr(call, "id", None) or not getattr(call, "name", None) or not isinstance(getattr(call, "input", None), dict):
            raise ProviderError("invalid_call", "The model returned an incomplete tool call")
        definition = next((t for t in self.list_tools(context, for_execution=True) if t["name"] == call.name), None)
        if definition is None:
            raise ProviderError("unauthorized_tool", "The model requested an unavailable tool")
        if call.name in self._mcp_definitions:
            from app.mcp.client import fingerprint
            pinned = context.extra.get('mcp_schemas', {}).get(call.name)
            if pinned and pinned != fingerprint(self._mcp_definitions[call.name]):
                raise ProviderError('mcp_schema_changed', 'MCP tool changed during this run; start a new turn after reviewing the connection')
        issue = next(Draft202012Validator(definition["input_schema"]).iter_errors(call.input), None)
        if issue is not None:
            # jsonschema's messages can include the entire submitted value.
            # Give the model a field and a constraint without echoing that value.
            if issue.validator == "required":
                problem = issue.message
            elif issue.validator == "type":
                problem = f"expected type {issue.validator_value}"
            elif issue.validator == "additionalProperties":
                problem = "use only fields defined in the schema"
            else:
                problem = f"failed the {issue.validator} constraint"
            raise ProviderError("invalid_arguments",
                f"Tool arguments do not match the registered schema at {issue.json_path[:160]} ({problem[:200]})")
        if self._bounded_mail_automation(context) and self._mail_calendar_tool(call.name):
            self._check_mail_calendar_args(call.input, context)

    async def execute_recorded(self, call, context):
        """Validate authorization/schema, admit durably, then execute at most once.

        A started receipt surviving an interruption has an unknown outcome and
        is never replayed automatically. This does not claim external exactly-once.
        """
        import hashlib
        import json
        from app.schemas.llm import ProviderError
        self.validate_call(call, context)
        if context.coordination is not None:
            await context.coordination.ensure_active(context)
        context.tool_call_id = call.id
        if self._receipt_sessions is None:
            return await self.execute(call.name, call.input, context)
        from sqlalchemy import select
        from sqlalchemy.exc import IntegrityError
        from app.models.model_policy import ToolExecutionReceipt
        from app.database import write_session
        digest = hashlib.sha256(json.dumps(call.input, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        async with write_session(self._receipt_sessions) as db:
            receipt = (await db.execute(select(ToolExecutionReceipt).where(
                ToolExecutionReceipt.user_id == context.user_id,
                ToolExecutionReceipt.request_id == context.request_id,
                ToolExecutionReceipt.call_id == call.id))).scalar_one_or_none()
            if receipt:
                if receipt.arguments_hash != digest or receipt.name != call.name:
                    raise ProviderError("call_conflict", "Tool identity was reused with different arguments")
                if receipt.status == "completed":
                    context.extra.setdefault('produced_files', []).extend(receipt.result.get('files', []))
                    return receipt.result["text"]
                raise ProviderError("tool_outcome_unknown", "An earlier tool execution has an unknown outcome; inspection is required")
            db.add(ToolExecutionReceipt(user_id=context.user_id, request_id=context.request_id,
                call_id=call.id, name=call.name, arguments_hash=digest, status="started"))
            try:
                await db.flush()
            except IntegrityError as exc:
                raise ProviderError("call_in_progress", "This tool execution is already admitted") from exc
        files_before = len(context.extra.get('produced_files', []))
        if context.effective_policy:
            import asyncio
            async with asyncio.timeout(context.effective_policy.remaining()):
                result = await self.execute(call.name, call.input, context)
        else:
            result = await self.execute(call.name, call.input, context)
        async with write_session(self._receipt_sessions) as db:
            receipt = (await db.execute(select(ToolExecutionReceipt).where(
                ToolExecutionReceipt.user_id == context.user_id,
                ToolExecutionReceipt.request_id == context.request_id,
                ToolExecutionReceipt.call_id == call.id))).scalar_one()
            receipt.status, receipt.result = "completed", {"text": result,
                'files': context.extra.get('produced_files', [])[files_before:]}
            if context.coordination:
                await context.coordination.persist_memory(db, context)
        return result

    async def execute(self, tool_name: str, args: dict, context: "AgentContext") -> str:
        """Route a tool call to the correct tier handler."""
        from app.schemas.llm import ProviderError
        if tool_name not in {tool['name'] for tool in self.list_tools(context, include_deferred=True, for_execution=True)}:
            raise ProviderError("unauthorized_tool", "The requested capability is not authorized")
        try:
            if tool_name == "Task":
                return await self._execute_task(args, context)

            if tool_name in self._skills:
                if context.db is None and self._receipt_sessions is not None:
                    from dataclasses import replace
                    async with self._receipt_sessions() as db:
                        return await self._skills[tool_name].execute(args, replace(context, db=db))
                return await self._skills[tool_name].execute(args, context)

            if tool_name in self._mcp_tool_map:
                server_name = self._mcp_tool_map[tool_name]
                original_name = self._mcp_original_names[tool_name]
                if self._bounded_mail_automation(context) and self._mail_calendar_tool(tool_name):
                    self._check_mail_calendar_args(args, context)
                    args = {**args, 'visibility':'private'}
                result = await self._mcp_clients[server_name].call_tool(original_name, args,
                    expected_schema=context.extra.get('mcp_schemas', {}).get(tool_name))
                if (self._bounded_mail_automation(context) and server_name == 'google_calendar'
                        and original_name == 'calendar_list_events' and args.get('calendar_id', 'primary') == 'primary'
                        and not args.get('query') and 'More events exist in this range.' not in result):
                    lower = self._calendar_instant(args.get('time_min'))
                    upper = self._calendar_instant(args.get('time_max'))
                    if lower and upper and lower < upper:
                        context.extra.setdefault('mail_calendar_checked', []).append((lower, upper))
                return result

            if tool_name in self._adapters:
                return await self._adapters[tool_name].execute(args, context)

            return f"Unknown tool: '{tool_name}'"

        except ProviderError:
            raise
        except Exception as e:
            logger.error(
                "tool_execution_error",
                extra={
                    "tool": tool_name,
                    "request_id": context.request_id,
                    "error_type": type(e).__name__,
                    "damage_control_handled": True,
                },
            )
            raise ProviderError("tool_execution_unknown",
                "Tool execution failed; its outcome requires inspection before retrying") from e

    async def _execute_task(self, args: dict, context: "AgentContext") -> str:
        """
        Tier 0 — delegate to the app-owned worker runtime.
        """
        if self._task_runner:
            return await self._task_runner(args, context)
        logger.info(
            "task_tool_called",
            extra={
                "request_id": context.request_id,
                "description": args.get("description", ""),
            },
        )
        return (
            "Task worker runtime is unavailable. No worker was started."
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
