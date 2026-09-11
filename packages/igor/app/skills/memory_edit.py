# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The bounded, evidenced topic editor. No positional insertion or blind rewrite."""
import json
from sqlalchemy import select
from app.models.memory_file import MemoryFile
from app.services.memory_admission import EVIDENCE_SCHEMA, purpose, resolve_evidence
from app.services.memory_states import version
from app.services.memory_store import mutate_file
from app.skills.base import Skill
from app.services.memory_schema import MemorySchemaViolation


class MemoryEditSkill(Skill):
    name = "memory_edit"
    read_only = False
    description = (
        "Read a topic's contract/content/version, or apply an EXACT evidenced patch. "
        "Raw memory edits are disabled. get first; put requires that version, an old "
        "substring occurring exactly once, its replacement, and exact source evidence. "
        "For new topic use version=new, old='', new=complete document. Topic purpose "
        "is enforced independently before commit. Completed actions use ledger_append; "
        "money uses finance_record; current situations use memory_state. For a move, "
        "ask Orion memory_audit to repair atomically. Never copy into a different domain "
        "after a refusal. message:latest refers ONLY to this owner conversation."
    )
    input_schema = {"type": "object", "properties": {
        "operation": {"type": "string", "enum": ["get", "put"]},
        "path": {"type": "string"}, "version": {"type": "string"},
        "old": {"type": "string"}, "new": {"type": "string"},
        "evidence": EVIDENCE_SCHEMA,
    }, "required": ["operation", "path"], "additionalProperties": False}

    async def execute(self, args, context):
        path = args.get("path", "")
        try:
            contract = purpose(path)
            file = (await context.db.execute(select(MemoryFile).where(
                MemoryFile.user_id == context.user_id, MemoryFile.path == path,
            ).execution_options(populate_existing=True))).scalar_one_or_none()
            before = file.content if file else None
            fingerprint = version(before) if before is not None else "new"
            if args.get("operation") == "get":
                return json.dumps({"path": path, "version": fingerprint,
                                   "contract": contract, "content": before}, ensure_ascii=False)
            if args.get("operation") != "put" or args.get("version") != fingerprint:
                raise ValueError("Missing/stale version or invalid operation. Get the document first.")
            if path.startswith(("/memories/states/", "/memories/finance/records/", "/memories/finance/ledger/")) or path == "/memories/finance/monthly-structure.md":
                raise ValueError("Use memory_state / finance_record for managed records and their computed views.")
            old, new = args.get("old"), args.get("new")
            if not isinstance(new, str) or not isinstance(old, str):
                raise ValueError("old and new must be strings.")
            if before is None:
                if old:
                    raise ValueError("New document requires old=''.")
                after = new
            else:
                if not old or before.count(old) != 1:
                    raise ValueError("old must match exactly once; reread and provide an unambiguous patch.")
                after = before.replace(old, new, 1)
            evidence = await resolve_evidence(context.db, context.user_id, args.get("evidence"), session_id=context.session_id)
            await mutate_file(context.db, user_id=context.user_id, path=path, before=before,
                              after=after, author=context.agent_id, action="topic_patch",
                              request_id=context.request_id, managed=True, evidence=evidence, model=context.model)
            return json.dumps({"written": path, "version": version(after)})
        except (ValueError, MemorySchemaViolation) as exc:
            return "Write rejected — " + str(exc)
