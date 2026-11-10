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


class SystemOpsSkill(Skill):
    name = "system_ops"
    deferred = True
    read_only = False
    memoize = False
    restricted_to = frozenset({"orion"})
    search_keywords = "host server system operations Docker service status incidents health maintenance"
    description = (
        "Maintain the actual Mark VI server for the owner. Use exec for host shell commands and read_file "
        "for accessible files. Use write_file for files under /var/lib/speda-orion-ops/workspace. "
        "Use services, incidents and service_logs to diagnose problems and restart_service for a named service "
        "when the owner asks or delegates the repair. For app restart, give the owner the operation ID and finish "
        "the turn. Confirm a scheduled or uncertain result with operation_status before repeating an operation."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["status", "services", "incidents", "exec", "read_file", "write_file",
                         "service_logs", "restart_service", "operation_status"],
                "default": "status",
            },
            "command": {"type": "string", "minLength": 1, "maxLength": 4000},
            "path": {"type": "string", "minLength": 1, "maxLength": 1000},
            "content": {"type": "string", "maxLength": 16000},
            "timeout": {"type": "integer", "minimum": 1, "maximum": 40, "default": 20},
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
                and context.triggered_by == "user")

    async def execute(self, args, context):
        if not self.available(context):
            return json.dumps({"status": "denied", "reason": "Available in the owner's Orion chat."})
        action = args.get("action", "status")
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
