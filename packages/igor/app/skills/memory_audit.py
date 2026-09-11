# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
import json
from app.skills.base import Skill


class MemoryAuditSkill(Skill):
    name = "memory_audit"
    description = (
        "Orion's measurable memory audit. run queues the durable controller, which reads "
        "pending/expired/unresolved documents, compares source evidence, applies bounded "
        "atomic repairs and records exact review fingerprints. status returns real progress, "
        "failures and remaining work; scan returns structural and semantic coverage. "
        "Never claim success from a scan alone. Manual success logs/attestations are disabled."
    )
    restricted_to = frozenset({"orion"})
    read_only = False
    input_schema = {"type": "object", "properties": {
        "operation": {"type": "string", "enum": ["scan", "run", "status"]},
    }, "required": ["operation"], "additionalProperties": False}

    async def execute(self, args, context):
        from app.services.memory_verify import verify_all
        if context.agent_id != "orion":
            return "Error: only Orion may attest memory reviews."
        if args.get("operation") == "scan":
            return json.dumps(await verify_all(context.db, context.user_id), ensure_ascii=False)
        if args.get("operation") == "run":
            from app.services.memory_audit_worker import enqueue_audit
            return json.dumps(await enqueue_audit(user_id=context.user_id, model=context.model,
                                                  request_id=context.request_id), ensure_ascii=False)
        if args.get("operation") == "status":
            from app.services.task_queue import latest_job
            return json.dumps(await latest_job("memory_audit", context.user_id), ensure_ascii=False)
        return "Reviews are recorded by the audit worker after it reads each document. Use operation=run, then status."
