# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Capture a meaningful personal experience, event, or reference into the owner's memory."""

import json
import logging
import hashlib
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
    description = (
        "Capture one source-supported personal experience or event in a dated memory record. "
        "Use it for a completed event without a more specific domain writer, and cite the "
        "owner's exact message or another verifiable source. Do not use it for an ongoing "
        "state, a person profile, or a speculative outcome. It returns a stable record ID "
        "and path that can be revisited through connected memory. Omit date when no "
        "occurrence date is known: the backend automatically files it in the current "
        "recording month in the owner's timezone, without a folder or date argument."
    )
    
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
                "description": "Optional evidenced occurrence date, YYYY-MM-DD. Omit to file in the current recording month automatically.",
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
            "related_entities": {"type":"array","items":{"type":"string"},"maxItems":4,
                                 "description":"Optional explicit names already known in memory; they are linked without creating duplicate identities."},
            "source_idempotency_key": {
                "type": "string",
                "description": "for deduplication",
            }
        },
        "required": ["summary", "evidence"],
        "additionalProperties": False
    }

    async def execute(self, args: dict, context: "AgentContext") -> str:
        from sqlalchemy import select
        from app.models.memory_record_meta import MemoryRecordMeta
        from app.models.memory_file import MemoryFile
        from app.services.memory_admission import resolve_evidence

        evidence = args.get("evidence", [])
        if not evidence or not all(isinstance(e, dict) and "ref" in e and "quote" in e for e in evidence):
            return "Reject: Missing evidence — you must provide exact supporting quotes with ref and quote."

        date_str = args.get("date")
        
        occurred_on = None
        if date_str:
            try:
                occurred_on = date.fromisoformat(date_str)
            except ValueError:
                return "Reject: Invalid date — you must provide a valid YYYY-MM-DD date."

        try:
            verified_evidence = await resolve_evidence(
                context.db, context.user_id, evidence,
                session_id=context.session_id,
            )
        except ValueError as exc:
            return f"Reject: {exc} Nothing saved."

        # One request may capture several events. An exact retry of the same
        # source and content gets the original ID, including across requests.
        source_id = args.get("source_idempotency_key")
        if not source_id:
            canonical = json.dumps({
                "summary": (args.get("summary") or "").strip(),
                "date": date_str, "kind": args.get("kind", "event"),
                "evidence": sorted((item["ref"], item["quote"])
                                   for item in verified_evidence),
            }, ensure_ascii=False, sort_keys=True)
            source_id = "event:" + hashlib.sha256(canonical.encode()).hexdigest()

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
            evidence=verified_evidence,
            summary=args.get("summary", ""),
            kind=args.get("kind", "event"),
            title=args.get("title"),
            occurred_on=occurred_on,
            occurred_until=None,
            effective_from=None,
            source_timestamp=source_timestamp,
            source_timezone=context.timezone,
            category_hint=args.get("category_hint", "general"),
            related_entities=args.get("related_entities"),
        )

        try:
            job = await create_candidate(context.db, context.user_id, candidate)
            await context.db.commit()  # intake survives a reviewer failure/restart
        except ValueError as exc:
            await context.db.rollback()
            return f"Reject: {exc} Nothing saved."
        if job.state == 'committed':
            record_ids = job.committed_record_ids or []
            if record_ids:
                row = (await context.db.execute(
                    select(MemoryRecordMeta.record_id, MemoryFile.path)
                    .join(MemoryFile, MemoryRecordMeta.memory_file_id == MemoryFile.id)
                    .where(MemoryRecordMeta.user_id == context.user_id,
                           MemoryRecordMeta.record_id == record_ids[0])
                )).first()
                if row:
                    return json.dumps({"path": row.path, "record_id": row.record_id,
                                       "already_recorded": True})
            return "Existing capture is marked committed but its record is missing; no new event was written."
        if job.state not in ('pending','retryable_failure'):
            return f"Capture is {job.state}; no new event was written."
        job_id,candidate_id=job.id,job.candidate_id
        if job.attempts>=3:
            return "Capture retry budget exhausted (3 attempts). Original input retained; no new reviewer call was made."
        from app.models.memory_capture_job import MemoryCaptureJob
        from sqlalchemy import update
        # Claim one unit durably. A crashed in-flight review is not auto-replayed.
        claimed = await context.db.execute(update(MemoryCaptureJob).where(
            MemoryCaptureJob.id==job_id,MemoryCaptureJob.user_id==context.user_id,
            MemoryCaptureJob.state.in_(("pending","retryable_failure")),MemoryCaptureJob.attempts<3,
        ).values(state="reviewing",attempts=MemoryCaptureJob.attempts+1))
        await context.db.commit()
        if claimed.rowcount!=1:
            return "Capture is already claimed or committed; no reviewer call was repeated."
        try:
            routing = await route_candidate(context.db, context.user_id, candidate,record_id=candidate_id)
            result = await commit_candidate(
            db=context.db, 
            user_id=context.user_id, 
            candidate=candidate, 
            routing=routing,
            author=context.agent_id,
            model=context.model,
            request_id=context.request_id,
            )
        except Exception as exc:
            await context.db.rollback()
            cause=exc
            while cause.__cause__ is not None:
                cause=cause.__cause__
            status=getattr(cause,"status_code",None)
            retryable = isinstance(cause,(TimeoutError,ConnectionError)) or status in (408,429,500,502,503,504)
            from app.services.memory_store import MemoryWriteConflict
            retryable = retryable or isinstance(exc,MemoryWriteConflict) or type(cause).__name__ in ("APITimeoutError","APIConnectionError")
            state="retryable_failure" if retryable else "needs_review"
            await context.db.execute(update(MemoryCaptureJob).where(
                MemoryCaptureJob.id==job_id,MemoryCaptureJob.user_id==context.user_id,
            ).values(state=state,error=str(exc)[:2000]))
            await context.db.commit()
            return f"Capture {state}: {exc}. Nothing saved to active memory; original input is retained."
        
        return json.dumps({
            "path": result["path"],
            "record_id": result["record_id"],
            "period": routing.period,
            "category": routing.category
        })
