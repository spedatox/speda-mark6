# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Memory capture service — candidate lifecycle and recovery intake.
Implements §6 of the Monthly Memory Architecture.
"""

import logging
import uuid
from dataclasses import asdict, dataclass
import json
from datetime import date, datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utc_now

from app.models.memory_capture_job import MemoryCaptureJob
from app.models.memory_capture_payload import MemoryCapturePayload
from app.models.memory_file import MemoryFile
from app.models.memory_record_meta import MemoryRecordMeta
from app.services.memory_calendar import month_for_event, TemporalResolution
from app.services.memory_identity import (
    content_hash,
    generate_record_id,
    collision_safe_slug,
)
from app.services.memory_paths import MonthPeriod, build_monthly_path, slugify
from app.services.memory_store import _mutate_in_txn

logger = logging.getLogger(__name__)

CAPTURE_STATES = (
    'pending', 
    'routed', 
    'reviewing', 
    'committed', 
    'rejected', 
    'needs_review', 
    'retryable_failure'
)


@dataclass
class CaptureCandidate:
    source_id: str
    evidence: list[dict]
    summary: str
    kind: str | None
    title: str | None
    occurred_on: date | None
    occurred_until: date | None
    effective_from: date | None
    source_timestamp: datetime | None
    source_timezone: str | None
    category_hint: str | None
    related_entities: list[str] | None


@dataclass
class RoutingResult:
    category: str
    period: str
    slug: str
    path: str
    entity_id: str | None
    occurrence_id: str | None
    record_id: str
    temporal: TemporalResolution
    is_new_file: bool
    related_entity_ids: tuple[str, ...] = ()


async def create_candidate(db: AsyncSession, user_id: int, candidate: CaptureCandidate) -> MemoryCaptureJob:
    """
    Check source-scoped idempotency: (user_id, source_id) uniqueness.
    If duplicate, return existing job.
    Create new MemoryCaptureJob in 'pending' state.
    """
    payload = json.loads(json.dumps(asdict(candidate), default=lambda value: value.isoformat()))
    payload["evidence"] = [{k:v for k,v in item.items() if k not in ("source_body", "_image")} for item in payload["evidence"]]
    identity = {k:v for k,v in payload.items() if k not in ("source_timestamp", "source_timezone")}
    identity_hash = content_hash(json.dumps(identity,sort_keys=True,ensure_ascii=False))
    result = await db.execute(
        select(MemoryCaptureJob).where(
            MemoryCaptureJob.user_id == user_id,
            MemoryCaptureJob.source_id == candidate.source_id
        )
    )
    job = result.scalars().first()
    if job is not None:
        stored = await db.get(MemoryCapturePayload,job.id)
        if stored is None:
            raise ValueError("Legacy capture has no complete payload; inspect its retained evidence rather than replaying it.")
        if stored.identity_hash != identity_hash:
            raise ValueError("Idempotency key already belongs to a different capture. Reuse the key only for its exact original content.")
        return job

    job = MemoryCaptureJob(
        user_id=user_id,
        source_id=candidate.source_id,
        candidate_id=uuid.uuid4().hex,
        original_evidence=candidate.evidence,
        kind=candidate.kind,
        category_hint=candidate.category_hint,
        state='pending',
        source_timestamp=candidate.source_timestamp,
        source_timezone=candidate.source_timezone,
    )
    db.add(job)
    await db.flush()
    db.add(MemoryCapturePayload(capture_job_id=job.id,user_id=user_id,
                               payload=payload,identity_hash=identity_hash))
    await db.flush()
    return job


async def route_candidate(db: AsyncSession, user_id: int, candidate: CaptureCandidate, *, record_id: str | None = None) -> RoutingResult:
    """
    Resolve the temporal period using month_for_event().
    Determine category, generate slug, check for collisions, resolve entity identity.
    """
    temporal = month_for_event(
        kind=candidate.kind or 'event',
        occurred_on=candidate.occurred_on,
        occurred_until=candidate.occurred_until,
        effective_from=candidate.effective_from,
        # Source time is provenance, not the time this record is saved. An
        # undated capture always belongs to the current recording month.
        recorded_at=utc_now(),
        source_timezone=candidate.source_timezone,
    )

    category = candidate.category_hint or 'general'
    
    entity_id = None
    related_entity_ids = []
    if candidate.related_entities and len(candidate.related_entities) > 0:
        # An event's filing category does not redefine the identity of someone
        # it mentions. Reuse known people/projects; never invent a second
        # "general concept" for a person already present in social/.
        from app.models.memory_entity import MemoryEntity
        entities = (await db.execute(select(MemoryEntity).where(
            MemoryEntity.user_id == user_id,
        ))).scalars().all()
        for name in candidate.related_entities[:4]:
            want = slugify(name)
            matches = [row.id for row in entities if want in {
                slugify(n) for n in [row.canonical_name, *(row.aliases if isinstance(row.aliases,list) else [])]
            }]
            if len(matches)>1:
                raise ValueError("Ambiguous related entity; inspect existing identities before linking this event.")
            if len(matches)==1 and matches[0] not in related_entity_ids:
                related_entity_ids.append(matches[0])
        entity_id = related_entity_ids[0] if related_entity_ids else None

    base_slug = slugify(candidate.title or candidate.summary[:20] or "untitled")
    
    period_obj = MonthPeriod.from_canonical(temporal.period)
    
    # For now, simplistic group assignment for social
    group = 'personal' if category == 'social' else None
    
    prefix = f"/memories/{category}/{period_obj.folder_name}/"
    if group:
        prefix += f"{group}/"
        
    stmt = select(MemoryFile.path).where(
        MemoryFile.user_id == user_id,
        MemoryFile.path.like(f"{prefix}%")
    )
    res = await db.execute(stmt)
    existing_paths = res.scalars().all()
    
    existing_slugs = set()
    for p in existing_paths:
        if p.endswith(".md"):
            filename = p.split("/")[-1]
            existing_slugs.add(filename[:-3])

    slug = collision_safe_slug(base_slug, existing_slugs, candidate.occurred_on)
    path = build_monthly_path(category, period_obj, slug, group=group)
    
    record_id = record_id or generate_record_id()
    is_new_file = path not in existing_paths
    
    occurrence_id = None
    if candidate.kind == 'event':
        occurrence_id = uuid.uuid5(uuid.NAMESPACE_URL,"memory-event:"+record_id).hex
        
    return RoutingResult(
        category=category,
        period=temporal.period,
        slug=slug,
        path=path,
        entity_id=entity_id,
        occurrence_id=occurrence_id,
        record_id=record_id,
        temporal=temporal,
        is_new_file=is_new_file,
        related_entity_ids=tuple(related_entity_ids),
    )


def build_event_markdown(candidate: CaptureCandidate, routing: RoutingResult) -> str:
    """
    Build a well-structured markdown document for the new event.
    """
    title = candidate.title or "Untitled Event"
    occurred = candidate.occurred_on.isoformat() if candidate.occurred_on else 'Unknown'
    
    refs = []
    for ev in candidate.evidence:
        if 'ref' in ev:
            refs.append(ev['ref'])
    ref_str = ", ".join(refs) if refs else "Unknown"
    
    return f"# {title}\n\n{candidate.summary}\n\n- Date: {occurred}\n- Source: {ref_str}\n"


async def commit_candidate(
    db: AsyncSession, 
    user_id: int, 
    candidate: CaptureCandidate, 
    routing: RoutingResult, 
    author: str = 'speda',
    model: str = '',
    request_id: str = '',
) -> dict:
    """
    Commit the document, ID metadata, and capture state as one transaction.
    """
    content = build_event_markdown(candidate, routing)
    
    before = None
    if not routing.is_new_file:
        res = await db.execute(
            select(MemoryFile).where(MemoryFile.user_id == user_id, MemoryFile.path == routing.path)
        )
        file_obj = res.scalars().first()
        if file_obj:
            before = file_obj.content
    
    try:
        await _mutate_in_txn(
            db, user_id=user_id, path=routing.path, before=before,
            after=content, author=author, action='commit', managed=True,
            record_id=routing.record_id, evidence=candidate.evidence,
            model=model, request_id=request_id,
        )
        res = await db.execute(select(MemoryFile.id).where(
            MemoryFile.user_id == user_id, MemoryFile.path == routing.path,
        ))
        file_id = res.scalar_one()

        meta = MemoryRecordMeta(
            memory_file_id=file_id,
            record_id=routing.record_id,
            user_id=user_id,
            entity_id=routing.entity_id,
            occurrence_id=routing.occurrence_id,
            category=routing.category,
            kind=candidate.kind or 'event',
            period=routing.temporal.period,
            period_basis=routing.temporal.period_basis.value,
            occurred_on=routing.temporal.occurred_on,
            occurred_until=routing.temporal.occurred_until,
            effective_from=routing.temporal.effective_from,
            effective_until=routing.temporal.effective_until,
            recorded_at=datetime.now(timezone.utc),
            source_recorded_at=candidate.source_timestamp,
            date_precision=routing.temporal.date_precision.value,
            source_timezone=routing.temporal.source_timezone,
            schema_version=3,
            version=1,
            content_hash=content_hash(content),
        )
        db.add(meta)
        await db.flush()
        from app.services.memory_graph import add_edge
        for linked in routing.related_entity_ids or ((routing.entity_id,) if routing.entity_id else ()):
            await add_edge(db,user_id,'record:'+routing.record_id,
                           'entity:'+linked,'mentions')
        stmt = update(MemoryCaptureJob).where(
            MemoryCaptureJob.user_id == user_id,
            MemoryCaptureJob.source_id == candidate.source_id
        ).values(
            state='committed', committed_record_ids=[routing.record_id],
            updated_at=datetime.now(timezone.utc)
        )
        await db.execute(stmt)
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    
    return {
        "path": routing.path,
        "record_id": routing.record_id,
        "version": 1
    }


async def reject_candidate(db: AsyncSession, user_id: int, source_id: str, reason: str) -> None:
    """
    Update capture job to 'rejected' state with reason.
    """
    stmt = update(MemoryCaptureJob).where(
        MemoryCaptureJob.user_id == user_id,
        MemoryCaptureJob.source_id == source_id
    ).values(
        state='rejected',
        error=reason,
        updated_at=datetime.now(timezone.utc)
    )
    await db.execute(stmt)
    await db.flush()
