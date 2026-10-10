"""Shared engineering inventory and project handoffs through the normal registry.

These are operational records, not owner-memory facts or agent-scoped chat
projects. Every agent can discover the same workshop; selecting a checkout binds
the conversation to a durable desk, which Task carries into its workers.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

from app.core.context import AgentContext
from app.skills.base import Skill


class WorkshopStatusSkill(Skill):
    name = "workshop_status"
    description = (
        "Lists the engineering workshop's registered projects or inspects one project's saved "
        "objective, checkpoint revision, next steps, blockers and recent Forge runs. Use it "
        "before continuing engineering work or answering where a project was left. Do not "
        "treat a running claim as proof a worker is alive, or a successful worker as a finished "
        "project; after a crash an operator must reconcile the old Cell. Returns project IDs, "
        "host paths, availability and durable execution reports without starting a worker."
    )
    read_only = True
    input_schema = {
        "type": "object", "properties": {
            "project_id": {"type": "string"},
            "execution_id": {"type": "string", "description": "Inspect any retained run, including one older than the project's recent-run list."},
            "offset": {"type": "integer", "minimum": 0},
        }, "additionalProperties": False,
    }

    def __init__(self, root: str = "", workspace_service=None, input_service=None):
        self._root = root
        self._workspaces = workspace_service
        self._inputs = input_service

    def _store(self, readonly=False):
        from forge.config import ForgeSettings
        from forge.workshop import Workshop
        root = Path(self._root).expanduser() if self._root else ForgeSettings.from_env().workspace_root
        return Workshop(root, readonly=readonly)

    async def execute(self, args: dict, context: AgentContext) -> str:
        try:
            store = await asyncio.to_thread(self._store, True)
            if args.get("execution_id"):
                result = await asyncio.to_thread(store.run_status, args["execution_id"])
            elif args.get("project_id"):
                result = await asyncio.to_thread(store.inspect, args["project_id"])
            else:
                result = await asyncio.to_thread(store.list_projects, offset=max(0, int(args.get("offset", 0))))
            return json.dumps(result, ensure_ascii=False)
        except (ValueError, OSError, sqlite3.Error) as exc:
            return f"Workshop unavailable: {exc}"


class WorkshopUpdateSkill(WorkshopStatusSkill):
    name = "workshop_update"
    read_only = False
    description = (
        "Discovers repositories under the configured Forge root, selects a registered project "
        "for this conversation's Task workers, or saves a durable project checkpoint. Use select before "
        "deploying an Autobot and checkpoint after evaluating its report, retaining the objective, "
        "acceptance criteria, progress, next steps, blockers and check evidence. Do not use it to "
        "change owner memory, release an interrupted worker, schedule jobs, or declare completion "
        "with outstanding work. A bound conversation cannot switch desks; start a new chat for another desk. "
        "Returns the project and new revision; checkpoint requires the "
        "revision read by workshop_status so concurrent updates cannot silently overwrite work."
    )
    input_schema = {
        "type": "object", "properties": {
            "action": {"type": "string", "enum": ["discover", "select", "checkpoint"]},
            "project_id": {"type": "string"},
            "workspace": {"type": "string", "description": "Existing workspace to register; otherwise use the selected chat workspace."},
            "revision": {"type": "integer", "minimum": 0},
            "state": {
                "type": "object", "properties": {
                    "name": {"type": "string"}, "objective": {"type": "string"},
                    "status": {"type": "string", "enum": ["active", "paused", "blocked", "complete"]},
                    "progress": {"type": "string"},
                    **{key: {"type": "array", "items": {"type": "string"}}
                       for key in ("acceptance", "next_steps", "blockers", "checks")},
                }, "required": ["objective", "status"], "additionalProperties": False,
            },
        }, "required": ["action"], "additionalProperties": False,
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        from app.execution.forge import _resolve_workspace
        try:
            store = await asyncio.to_thread(self._store)
            action = args.get("action")
            if action == "discover":
                result = await asyncio.to_thread(store.discover)
                result["inventory"] = await asyncio.to_thread(store.list_projects)
                return json.dumps(result, ensure_ascii=False)
            if action not in {"select", "checkpoint"}:
                raise ValueError("Unknown workshop action")
            if args.get("project_id"):
                project = await asyncio.to_thread(store.inspect, args["project_id"])
                workspace = _resolve_workspace(project["path"], allowed_root=self._root or None)
            else:
                value = args.get("workspace") or context.extra.get("cwd")
                if not value:
                    raise ValueError("Select a project ID or supply an existing workspace")
                workspace = _resolve_workspace(str(value), allowed_root=self._root or None)
            if action == "select":
                if self._workspaces is not None and self._root:
                    binding = await self._workspaces.prepare(
                        context, project_id=args.get("project_id"), workspace=str(workspace), explicit=True,
                    )
                    if self._inputs is not None:
                        await self._inputs.prepare(context)
                    result = await asyncio.to_thread(store.inspect, binding.project_id)
                    result["selected_for_session"] = context.session_id
                    return json.dumps(result, ensure_ascii=False)
                result = await asyncio.to_thread(store.register, workspace)
                context.extra["cwd"] = str(workspace)
                result["selected_for_this_turn"] = True
            else:
                if "revision" not in args or not isinstance(args.get("state"), dict):
                    raise ValueError("Checkpoint needs the last inspected revision and a complete state")
                result = await asyncio.to_thread(store.checkpoint, workspace,
                                                 revision=args["revision"], state=args["state"])
            return json.dumps(result, ensure_ascii=False)
        except (ValueError, OSError, sqlite3.Error) as exc:
            return f"Workshop update refused: {exc}"
