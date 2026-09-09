# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
import json
from app.skills.base import Skill


class MemoryAuditSkill(Skill):
    name = "memory_audit"
    description = (
        "Orion's accountable memory audit. scan returns structural findings, overdue "
        "states, unresolved semantic defects and EVERY document not reviewed since its "
        "last edit. Read each pending document and relevant evidence. Check subject "
        "placement, state vs event, conflicting editions, stale claims and broken links. "
        "Repair only evidenced defects, then scan again and review the NEW fingerprint. "
        "review records concrete unresolved findings (or []), with rationale and exact "
        "fingerprint from scan. Never mark unseen documents reviewed. A formatting "
        "check passing is not a semantic review; pending_review must not be called clean."
    )
    restricted_to = frozenset({"orion"})
    read_only = False
    input_schema = {"type": "object", "properties": {
        "operation": {"type": "string", "enum": ["scan", "review"]},
        "path": {"type": "string"}, "fingerprint": {"type": "string"},
        "findings": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"},
    }, "required": ["operation"], "additionalProperties": False}

    async def execute(self, args, context):
        from app.services.memory_audit import record_review
        from app.services.memory_verify import verify_all
        if context.agent_id != "orion":
            return "Error: only Orion may attest memory reviews."
        if args.get("operation") == "scan":
            return json.dumps(await verify_all(context.db, context.user_id), ensure_ascii=False)
        if args.get("operation") != "review":
            return "Error: operation must be scan or review."
        try:
            await record_review(context.db, user_id=context.user_id, author=context.agent_id,
                                path=args.get("path"), fingerprint=args.get("fingerprint"),
                                findings=args.get("findings"), rationale=args.get("rationale"))
        except ValueError as exc:
            return "Review rejected — " + str(exc)
        return "Review saved against those exact bytes. Repairs invalidate it until re-reviewed."
