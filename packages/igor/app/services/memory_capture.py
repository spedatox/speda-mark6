# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Memory capture service — candidate lifecycle and recovery intake.
Implements §6 of the Monthly Memory Architecture.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memory_capture_job import MemoryCaptureJob
from app.models.memory_file import MemoryFile
from app.models.memory_record_meta import MemoryRecordMeta
from app.services.memory_calendar import month_for_event, TemporalResolution
from app.services.memory_identity import (
    content_hash,
    find_or_create_entity,
    generate_record_id,
    collision_safe_slug,
    generate_occurrence_id,
)
from app.services.memory_paths import MonthPeriod, build_monthly_path, slugify
from app.services.memory_store import mutate_file

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


async def create_candidate(db: AsyncSession, user_id: int, candidate: CaptureCandidate) -> MemoryCaptureJob:
    """
    Check source-scoped idempotency: (user_id, source_id) uniqueness.
    If duplicate, return existing job.
    Create new MemoryCaptureJob in 'pending' state.
    """
    result = await db.execute(
        select(MemoryCaptureJob).where(
            MemoryCaptureJob.user_id == user_id,
            MemoryCaptureJob.source_id == candidate.source_id
        )
    )
    job = result.scalar_first()
    if job is not None:
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
    return job


async def route_candidate(db: AsyncSession, user_id: int, candidate: CaptureCandidate) -> RoutingResult:
    """
    Resolve the temporal period using month_for_event().
    Determine category, generate slug, check for collisions, resolve entity identity.
    """
    temporal = month_for_event(
        kind=candidate.kind or 'event',
        occurred_on=candidate.occurred_on,
        occurred_until=candidate.occurred_until,
        effective_from=candidate.effective_from,
        recorded_at=candidate.source_timestamp,
        source_timezone=candidate.source_timezone,
    )

    category = candidate.category_hint or 'general'
    
    entity_id = None
    if candidate.related_entities and len(candidate.related_entities) > 0:
        canonical_name = candidate.related_entities[0]
        entity_id, _ = await find_or_create_entity(
            db=db,
            user_id=user_id,
            category=category,
            entity_type='person' if category == 'social' else 'concept',
            canonical_name=canonical_name
        )

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
    
    record_id = generate_record_id()
    is_new_file = path not in existing_paths
    
    occurrence_id = None
    if candidate.kind == 'event' and entity_id:
        occurrence_id = generate_occurrence_id()
        
    return RoutingResult(
        category=category,
        period=temporal.period,
        slug=slug,
        path=path,
        entity_id=entity_id,
        occurrence_id=occurrence_id,
        record_id=record_id,
        temporal=temporal,
        is_new_file=is_new_file
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
    author: str = 'speda'
) -> dict:
    """
    Use mutate_file to write content, create MemoryRecordMeta, and update job state.
    """
    content = build_event_markdown(candidate, routing)
    
    before = None
    if not routing.is_new_file:
        res = await db.execute(
            select(MemoryFile).where(MemoryFile.user_id == user_id, MemoryFile.path == routing.path)
        )
        file_obj = res.scalar_first()
        if file_obj:
            before = file_obj.content
    
    await mutate_file(
        db,
        user_id=user_id,
        path=routing.path,
        before=before,
        after=content,
        author=author,
        action='commit',
        record_id=routing.record_id,
        evidence=candidate.evidence
    )
    
    res = await db.execute(
        select(MemoryFile.id).where(MemoryFile.user_id == user_id, MemoryFile.path == routing.path)
    )
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
        schema_version=1,
        version=1,
        content_hash=content_hash(content)
    )
    db.add(meta)
    
    stmt = update(MemoryCaptureJob).where(
        MemoryCaptureJob.user_id == user_id,
        MemoryCaptureJob.source_id == candidate.source_id
    ).values(
        state='committed',
        updated_at=datetime.now(timezone.utc)
    )
    await db.execute(stmt)
    
    await db.flush()
    
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
