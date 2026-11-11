"""Orion host maintenance and operational diagnostics."""

import asyncio
import json
import re

import httpx

from app.services.host_operations import host_operation
from app.skills.base import Skill


_SERVICE_NAME = re.compile(
    r"^/speda-((renovated-app|hisar|n8n|sandbox|caddy|browser|playwright-mcp)-[0-9]+|postgres)$"
)
_COLLABORATION_ACTIONS = frozenset({"status", "services", "incidents", "diagnostics", "service_logs"})


class SystemOpsSkill(Skill):
    name = "system_ops"
    deferred = True
    read_only = False
    memoize = False
    restricted_to = frozenset({"orion"})
    search_keywords = "host server system operations Docker service status incidents health maintenance"
    description = (
        "Operate the live Mark VI server. In a direct owner turn, exec runs any Bash command as root "
        "on the host, and read_file and write_file work with absolute host paths. Commands have no "
        "runtime limit unless you supply a timeout. In an owner-started collaboration, use status, services, "
        "incidents, diagnostics and service_logs to gather evidence for your colleague. Use restart_service "
        "for a managed restart. For an app restart, give the owner the operation ID and finish the turn. "
        "Use operation_status to check a running, scheduled or uncertain command before repeating it."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["status", "services", "incidents", "diagnostics", "exec", "read_file", "write_file",
                         "service_logs", "restart_service", "operation_status"],
                "default": "status",
            },
            "command": {"type": "string", "minLength": 1},
            "path": {"type": "string", "minLength": 1},
            "content": {"type": "string"},
            "timeout": {"type": "integer", "minimum": 1, "description": "Optional command deadline in seconds."},
            "diagnostic": {"enum": ["load", "storage", "listeners", "failed_units"]},
            "service": {"type": "string", "enum": ["app", "n8n", "caddy", "sandbox",
                        "browser", "playwright-mcp", "hisar", "orion-spark"]},
            "operation_id": {"type": "string", "pattern": "^[a-f0-9]{32}$"},
        },
        "additionalProperties": False,
    }

    def __init__(self, journal):
        self.journal = journal

    def available(self, context):
        return (getattr(context, "user_id", None) == 1
                and context.agent_id == "orion" and context.task_role == "interactive"
                and (context.triggered_by == "user" or (
                    context.triggered_by == "agent" and context.extra.get("owner_collaboration")
                )))

    async def execute(self, args, context):
        if not self.available(context):
            return json.dumps({"status": "denied", "reason": "Available to Orion in owner-started conversations."})
        action = args.get("action", "status")
        if context.triggered_by != "user" and action not in _COLLABORATION_ACTIONS:
            return json.dumps({"status": "denied", "reason": "Collaborations can run read-only host diagnostics."})
        if action == "incidents":
            if self.journal is None:
                return json.dumps({"status": "unavailable", "reason": "Damage Control journal unavailable."})
            from app.services.incident_journal import JournalView
            report = await asyncio.to_thread(JournalView(self.journal).list, reviewed=True,
                                             severity="critical", search="service_", limit=10, offset=0)
            return json.dumps({"status": "ok", **report}, ensure_ascii=False)
        if action not in {"status", "services"}:
            response = await host_operation(args, context)
            return json.dumps(response, ensure_ascii=False)
        try:
            async with asyncio.timeout(4), httpx.AsyncClient(
                transport=httpx.AsyncHTTPTransport(uds="/var/run/docker.sock"),
                base_url="http://docker", trust_env=False,
            ) as client:
                if action == "status":
                    reply = await client.get("/info")
                    reply.raise_for_status()
                    info = reply.json()
                    return json.dumps({"status": "ok", "source": "docker_engine", "host": {
                        "os": info["OSType"], "docker_version": info["ServerVersion"],
                        "logical_cpus": info["NCPU"], "memory_total_bytes": info["MemTotal"],
                    }})
                reply = await client.get("/containers/json", params={
                    "all": 1, "filters": json.dumps({"name": [_SERVICE_NAME.pattern]}),
                })
                reply.raise_for_status()
                services = [
                    {"name": name[1:], "state": row["State"], "status": row["Status"]}
                    for row in reply.json() for name in row.get("Names", [])
                    if _SERVICE_NAME.fullmatch(name)
                ]
                return json.dumps({"status": "ok", "source": "docker_engine",
                                   "services": sorted(services, key=lambda row: row["name"])[:20]})
        except (httpx.HTTPError, OSError, TimeoutError, ValueError, KeyError, TypeError):
            return json.dumps({"status": "unavailable", "reason": "Local Docker Engine diagnostics unavailable."})
