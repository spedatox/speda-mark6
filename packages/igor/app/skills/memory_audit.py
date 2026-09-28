# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
import json
from app.skills.base import Skill


class MemoryAuditSkill(Skill):
    name = "memory_audit"
    description = (
        "Read-only memory coverage scan. Use it to inspect pending reviews and "
        "unresolved findings when the owner asks about memory state. It does not "
        "review, repair or schedule any memory items. It returns structural and "
        "semantic coverage; never claim a clean store from a partial scan."
    )
    restricted_to = frozenset({"orion"})
    read_only = True
    input_schema = {"type": "object", "properties": {
        "operation": {"type": "string", "enum": ["scan"]},
    }, "required": ["operation"], "additionalProperties": False}

    async def execute(self, args, context):
        from app.services.memory_verify import verify_all
        if context.agent_id != "orion":
            return "Error: only Orion may attest memory reviews."
        if args.get("operation") == "scan":
            return json.dumps(await verify_all(context.db, context.user_id), ensure_ascii=False)
        return "Only the read-only scan operation remains available."
