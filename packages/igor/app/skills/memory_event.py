# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Capture a meaningful personal experience, event, or reference into the owner's memory."""

import json
import logging
from datetime import datetime, date
from typing import TYPE_CHECKING

from app.skills.base import Skill
from app.services.memory_capture import (
    CaptureCandidate,
    create_candidate,
    route_candidate,
    commit_candidate
)

if TYPE_CHECKING:
    from app.core.context import AgentContext

logger = logging.getLogger(__name__)


class MemoryEventSkill(Skill):
    name = "memory_event"
    read_only = False
    description = "Capture a meaningful personal experience, event, or reference into the owner's memory"
    
    input_schema = {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "what happened, factually and concisely",
            },
            "evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "ref": {"type": "string"},
                        "quote": {"type": "string"}
                    },
                    "required": ["ref", "quote"]
                },
                "description": "exact supporting quotes",
            },
            "title": {
                "type": "string",
                "description": "short descriptive title for the document",
            },
            "date": {
                "type": "string",
                "description": "when it occurred, YYYY-MM-DD",
            },
            "date_unknown": {
                "type": "boolean",
                "description": "set true when the exact date is not known",
            },
            "kind": {
                "type": "string",
                "enum": ["event", "reference", "edition"],
                "description": "'event' | 'reference' | 'edition'",
            },
            "category_hint": {
                "type": "string",
                "description": "target category if known (default: general)",
            },
            "source_idempotency_key": {
                "type": "string",
                "description": "for deduplication",
            }
        },
        "required": ["summary", "evidence"],
        "additionalProperties": False
    }

    async def execute(self, args: dict, context: "AgentContext") -> str:
        evidence = args.get("evidence", [])
        if not evidence or not all(isinstance(e, dict) and "ref" in e and "quote" in e for e in evidence):
            return "Reject: Missing evidence — you must provide exact supporting quotes with ref and quote."

        date_str = args.get("date")
        date_unknown = args.get("date_unknown", False)
        
        occurred_on = None
        if date_str:
            try:
                occurred_on = date.fromisoformat(date_str)
            except ValueError:
                return "Reject: Invalid date — you must provide a valid YYYY-MM-DD date."
        elif not date_unknown:
            return "Reject: Invalid date — you must provide a 'date' or set 'date_unknown' to true."

        # Deduplication key
        source_id = args.get("source_idempotency_key")
        if not source_id:
            source_id = context.request_id

        # Use the source message's timestamp as source_timestamp, not the system clock at execution time
        source_timestamp = None
        ts_val = context.trigger_payload.get("timestamp")
        if ts_val:
            try:
                source_timestamp = datetime.fromisoformat(str(ts_val).replace("Z", "+00:00"))
            except ValueError:
                pass

        candidate = CaptureCandidate(
            source_id=source_id,
            evidence=evidence,
            summary=args.get("summary", ""),
            kind=args.get("kind", "event"),
            title=args.get("title"),
            occurred_on=occurred_on,
            occurred_until=None,
            effective_from=None,
            source_timestamp=source_timestamp,
            source_timezone=context.timezone,
            category_hint=args.get("category_hint", "general"),
            related_entities=None,
        )

        job = await create_candidate(context.db, context.user_id, candidate)
        
        # Determine routing regardless, as we might need to tell them the existing path
        routing = await route_candidate(context.db, context.user_id, candidate)

        if job.state != 'pending':
            return f"already recorded at {routing.path}"

        result = await commit_candidate(
            db=context.db, 
            user_id=context.user_id, 
            candidate=candidate, 
            routing=routing,
            author=context.agent_id
        )
        
        return json.dumps({
            "path": result["path"],
            "record_id": result["record_id"],
            "period": routing.period,
            "category": routing.category
        })
