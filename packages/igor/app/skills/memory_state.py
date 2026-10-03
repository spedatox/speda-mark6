# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Lifecycle transitions, never arbitrary edits to the current snapshot."""

import json
import re

from sqlalchemy import select

from app.core.clock import owner_today
from app.models.memory_file import MemoryFile
from app.services import memory_states as states
from app.services.memory_store import mutate_file
from app.services.memory_schema import MemorySchemaViolation
from app.skills.base import Skill
from app.services.memory_admission import EVIDENCE_SCHEMA, resolve_evidence


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
        "cross-agent reminder linked to that evidence. list/get are read-only and "
        "include freshness; list pages use next_offset, and get excerpts can be "
        "continued with memory view on the returned path."
    )
    read_only = False
    memoizable_operations = frozenset({"list", "get"})
    input_schema = {
        "type": "object",
        "properties": {
            "evidence": EVIDENCE_SCHEMA,
            "operation": {"type": "string", "enum": ["list", "get", "put"]},
            "key": {"type": "string"},
            "version": {"type": "string", "description": "get/list version, or 'new'."},
            "summary": {"type": "string", "description": "Present situation, not a completed action."},
            "status": {"type": "string", "enum": list(states.STATUSES)},
            "source": {"type": "string", "description": "Evidence reference: memory path, source:<id>, observation:<id>, or message:<id>. message:latest is pinned to the owner message matching the supplied evidence quote; include what supports this situation/outcome."},
            "review_on": {"type": "string", "description": "YYYY-MM-DD when the situation must be reverified. Choose from its actual timeline."},
            "starts_on": {"type": "string"},
            "ends_on": {"type": "string", "description": "Last known valid date, inclusive; omit if unknown."},
            "salience": {"type": "string", "enum": ["high", "normal", "low"], "description": "High only when the owner explicitly identifies this situation as central; affects bounded recall ordering, never factual truth."},
            "offset": {"type": "integer", "minimum": 0, "description": "List page offset; use next_offset from the previous result."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
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
            try:
                offset = max(0, int(args.get("offset", 0)))
                limit = min(20, max(1, int(args.get("limit", 10))))
            except (ValueError, TypeError):
                return "Error: offset and limit must be integers."
            # Return one current head per stable key, including monthly legacy
            # paths. The source text remains accessible by bounded memory view.
            heads, invalid = states.state_heads(rows)
            today = owner_today()
            items = [{"path": row.path, "key": key, "version": states.version(row.content),
                      "status": record["status"], "freshness": states.freshness(record, today),
                      "summary": record["summary"][:400], "verified_on": record["verified_on"]}
                     for key, (row, record) in heads.items()]
            items += [{"path": row.path, "version": states.version(row.content), "freshness": "invalid"} for row in invalid]
            items.sort(key=lambda item: item["path"])
            page = []
            for item in items[offset:offset+limit]:
                if page and len(json.dumps(page+[item], ensure_ascii=False)) > 5200:
                    break
                page.append(item)
            end = offset + len(page)
            return json.dumps({"items": page, "total": len(items), "next_offset": end if end < len(items) else None}, ensure_ascii=False)
        key = args.get("key", "")
        if not isinstance(key, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", key):
            return "Error: use a stable lowercase-hyphenated state key."
        path = states.resolve_state_path(rows, key) or f"{states.ROOT}{key}.md"
        file = next((f for f in rows if f.path == path), None)
        if op == "get":
            from app.skills.memory import _page
            result = {"path": path, "version": states.version(file.content) if file else "new",
                      "content": _page(path, file.content, {}, budget=2600) if file else ""}
            if file:
                try:
                    result["freshness"] = states.freshness(states.parse(file.content), owner_today())
                except (ValueError, TypeError, KeyError):
                    result["freshness"] = "invalid"
            return json.dumps(result, ensure_ascii=False)
        before = file.content if file else None
        expected = states.version(before) if before is not None else "new"
        if args.get("version") != expected:
            return "Error: missing or stale version. Get the state and review it before changing it. Nothing was saved."
        record = {k: args[k] for k in ("key", "summary", "status", "source", "review_on",
                                       "starts_on", "ends_on", "closed_on", "salience") if args.get(k)}
        record.update(verified_on=owner_today().isoformat(), author=context.agent_id,
                      session_id=context.session_id, request_id=context.request_id)
        try:
            if record.get("closed_on", "") > owner_today().isoformat():
                raise ValueError("A future outcome is a plan, not a completed/closed state.")
            after = states.encode(record)
            pin_latest = bool(re.search(r"message:latest\b", record["source"]))
            if not pin_latest:
                await states.verify_source(context.db, context.user_id, record["source"])
            evidence = await resolve_evidence(context.db, context.user_id, args.get("evidence"), session_id=context.session_id)
            if pin_latest:
                messages = {item["ref"] for item in evidence if re.fullmatch(r"message:\d+", item["ref"])}
                if len(messages) != 1:
                    raise ValueError("message:latest source requires one unambiguous evidenced owner message; supply its explicit message:<id> otherwise.")
                record["source"] = re.sub(r"message:latest\b", next(iter(messages)), record["source"])
                after = states.encode(record)
                await states.verify_source(context.db, context.user_id, record["source"])
            await mutate_file(context.db, user_id=context.user_id, path=path,
                              before=before, after=after, author=context.agent_id,
                              action="state_transition", request_id=context.request_id,
                              managed=True, evidence=evidence, model=context.model)
        except (ValueError, MemorySchemaViolation) as exc:
            return "Write rejected — " + str(exc)
        return json.dumps({"written": path, "version": states.version(after),
                           "status": record["status"]})
