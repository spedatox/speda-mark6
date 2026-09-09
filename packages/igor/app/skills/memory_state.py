# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Lifecycle transitions, never arbitrary edits to the current snapshot."""

import json

from sqlalchemy import select

from app.core.clock import owner_today
from app.models.memory_file import MemoryFile
from app.services import memory_states as states
from app.services.memory_store import mutate_file
from app.services.memory_schema import MemorySchemaViolation
from app.skills.base import Skill


class MemoryStateSkill(Skill):
    name = "memory_state"
    description = (
        "List, open, update or close ONGOING situations and confirmed plans. "
        "current.md is computed from these records; never write that file. "
        "An action already done belongs in ledger_append/registry_upsert, not here. "
        "Use a stable key per situation. get/list returns a version required for updates; "
        "use version='new' only for a new key. Supply source evidence, review_on, and "
        "status. A review date expiring means unverified, NEVER completed. Closing "
        "requires an outcome source and closed_on; records remain readable. "
        "Domain details remain in the domain document; a state is only a concise "
        "cross-agent reminder linked to that evidence. list/get are read-only."
    )
    read_only = False
    input_schema = {
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["list", "get", "put"]},
            "key": {"type": "string"},
            "version": {"type": "string", "description": "get/list version, or 'new'."},
            "summary": {"type": "string", "description": "Present situation, not a completed action."},
            "status": {"type": "string", "enum": list(states.STATUSES)},
            "source": {"type": "string", "description": "Evidence reference: memory path, observation id, or conversation message; include what supports this situation/outcome."},
            "review_on": {"type": "string", "description": "YYYY-MM-DD when the situation must be reverified. Choose from its actual timeline."},
            "starts_on": {"type": "string"},
            "ends_on": {"type": "string", "description": "Last known valid date, inclusive; omit if unknown."},
            "closed_on": {"type": "string", "description": "YYYY-MM-DD; required for completed/cancelled/superseded."},
        },
        "required": ["operation"],
        "additionalProperties": False,
    }

    async def execute(self, args, context):
        op = args.get("operation")
        if op not in ("get", "list", "put"):
            return "Error: operation must be list, get or put."
        rows = (await context.db.execute(select(MemoryFile).where(
            MemoryFile.user_id == context.user_id,
            MemoryFile.path.startswith(states.ROOT),
        ).execution_options(populate_existing=True))).scalars().all()
        if op == "list":
            return json.dumps([{"path": f.path, "version": states.version(f.content),
                                "content": f.content} for f in rows], ensure_ascii=False)
        key = args.get("key", "")
        path = f"{states.ROOT}{key}.md"
        file = next((f for f in rows if f.path == path), None)
        if op == "get":
            return json.dumps({"path": path, "version": states.version(file.content) if file else "new",
                               "content": file.content if file else ""}, ensure_ascii=False)
        before = file.content if file else None
        expected = states.version(before) if before is not None else "new"
        if args.get("version") != expected:
            return "Error: missing or stale version. Get the state and review it before changing it. Nothing was saved."
        record = {k: args[k] for k in ("key", "summary", "status", "source", "review_on",
                                       "starts_on", "ends_on", "closed_on") if args.get(k)}
        record.update(verified_on=owner_today().isoformat(), author=context.agent_id,
                      session_id=context.session_id, request_id=context.request_id)
        try:
            if record.get("closed_on", "") > owner_today().isoformat():
                raise ValueError("A future outcome is a plan, not a completed/closed state.")
            after = states.encode(record)
            await states.verify_source(context.db, context.user_id, record["source"])
            await mutate_file(context.db, user_id=context.user_id, path=path,
                              before=before, after=after, author=context.agent_id,
                              action="state_transition", request_id=context.request_id,
                              managed=True)
        except (ValueError, MemorySchemaViolation) as exc:
            return "Write rejected — " + str(exc)
        return json.dumps({"written": path, "version": states.version(after),
                           "status": record["status"]})
