"""Owner-scoped operational diagnostics over the local Docker Engine and journal."""

import asyncio
import json
import re

import httpx

from app.skills.base import Skill


_SERVICE_NAME = re.compile(
    r"^/speda-((renovated-app|hisar|n8n|sandbox|caddy|browser|playwright-mcp)-[0-9]+|postgres)$"
)


class SystemOpsSkill(Skill):
    name = "system_ops"
    deferred = True
    read_only = True
    memoize = False
    restricted_to = frozenset({"orion"})
    search_keywords = "host server system operations Docker service status incidents health maintenance"
    description = (
        "Inspect Mark VI host capacity, SPEDA service states and recorded service incidents. "
        "Use it for an owner-requested check of container health, the Docker host or a recent outage. "
        "Use system_info for disk, available memory and uptime readings from the backend runtime. "
        "Returns bounded read-only data. It cannot run commands or change services."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["status", "services", "incidents"],
                "default": "status",
            },
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
            return json.dumps({"status": "denied", "reason": "Unknown System Ops action."})
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
