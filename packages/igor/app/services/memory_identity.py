# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Identity resolution for the Monthly Memory Architecture.

Implements entity, occurrence, and record identity resolution as per design §5.
"""

import hashlib
import uuid
from datetime import date

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

# Forward-looking imports for models created in parallel
from app.models.memory_entity import MemoryEntity
from app.models.memory_entity_head import MemoryEntityHead
from app.models.memory_record_meta import MemoryRecordMeta


def generate_record_id() -> str:
    """UUID4 as hex string for record identity."""
    return uuid.uuid4().hex


def generate_entity_id() -> str:
    """UUID4 as hex string for entity identity."""
    return uuid.uuid4().hex


def generate_occurrence_id() -> str:
    """UUID4 as hex string for occurrence identity."""
    return uuid.uuid4().hex


def content_hash(content: str) -> str:
    """SHA-256 of UTF-8 encoded content. Same as memory_states.version()."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def collision_safe_slug(base_slug: str, existing_slugs: set[str], occurred_on: date | None = None) -> str:
    """
    Given a base slug and the set of existing slugs in the same directory:
    - If base_slug is not taken, return it
    - If taken and occurred_on is known, try {base_slug}-{day}
    - If still taken, append a short stable ID suffix: {base_slug}-{short_hex}
    - Never silently append content to an unrelated file
    """
    if base_slug not in existing_slugs:
        return base_slug

    if occurred_on:
        day_slug = f"{base_slug}-{occurred_on.day:02d}"
        if day_slug not in existing_slugs:
            return day_slug

    # If still taken, use short hex (first 6 chars of a UUID4)
    while True:
        short_hex = uuid.uuid4().hex[:6]
        hex_slug = f"{base_slug}-{short_hex}"
        if hex_slug not in existing_slugs:
            return hex_slug


async def resolve_entity_by_name(db: AsyncSession, user_id: int, category: str, canonical_name: str) -> str | None:
    """
    Look up an existing entity ID by (user_id, category, canonical_name) in memory_entities table.
    Also checks aliases.
    """
    # SQLite JSON contains workaround using LIKE since aliases is expected to be a JSON array string
    stmt = select(MemoryEntity.id).where(
        MemoryEntity.user_id == user_id,
        MemoryEntity.category == category,
        or_(
            MemoryEntity.canonical_name == canonical_name,
            MemoryEntity.aliases.like(f'%"{canonical_name}"%')
        )
    )
    result = await db.execute(stmt)
    return result.scalar_first()


async def find_or_create_entity(db: AsyncSession, user_id: int, category: str, entity_type: str, canonical_name: str) -> tuple[str, bool]:
    """
    Returns (entity_id, created). If entity exists, return it. Otherwise create.
    """
    entity_id = await resolve_entity_by_name(db, user_id, category, canonical_name)
    if entity_id is not None:
        return entity_id, False

    new_id = generate_entity_id()
    new_entity = MemoryEntity(
        id=new_id,
        user_id=user_id,
        category=category,
        entity_type=entity_type,
        canonical_name=canonical_name,
        aliases="[]"
    )
    db.add(new_entity)
    await db.flush()
    return new_id, True


async def resolve_occurrence(db: AsyncSession, user_id: int, entity_id: str, period: str, evidence_summary: str | None = None) -> tuple[str, bool]:
    """
    Find an existing occurrence of this entity in this period, or create a new one.
    Two trips to the same city in the same month are DIFFERENT occurrences.
    """
    stmt = select(MemoryOccurrence.id).where(
        MemoryOccurrence.user_id == user_id,
        MemoryOccurrence.entity_id == entity_id,
        MemoryOccurrence.period == period,
    )
    if evidence_summary is not None:
        stmt = stmt.where(MemoryOccurrence.evidence_summary == evidence_summary)
        
    result = await db.execute(stmt)
    occurrence_id = result.scalar_first()
    if occurrence_id is not None:
        return occurrence_id, False

    new_id = generate_occurrence_id()
    new_occurrence = MemoryOccurrence(
        id=new_id,
        user_id=user_id,
        entity_id=entity_id,
        period=period,
        evidence_summary=evidence_summary
    )
    db.add(new_occurrence)
    await db.flush()
    return new_id, True


async def get_entity_head(db: AsyncSession, user_id: int, entity_id: str) -> tuple[str | None, int]:
    """
    Returns (current_edition_id, version) from memory_entity_heads table.
    Returns (None, 0) if no head exists.
    """
    stmt = select(MemoryEntityHead.current_edition_id, MemoryEntityHead.version).where(
        MemoryEntityHead.user_id == user_id,
        MemoryEntityHead.entity_id == entity_id
    )
    result = await db.execute(stmt)
    row = result.first()
    if row is None:
        return None, 0
    return row.current_edition_id, row.version


async def update_entity_head(db: AsyncSession, user_id: int, entity_id: str, new_edition_id: str, expected_version: int) -> bool:
    """
    CAS update of entity head. Returns True on success, False on version conflict.
    """
    if expected_version == 0:
        # Create new head, if one doesn't exist
        existing = await get_entity_head(db, user_id, entity_id)
        if existing[0] is not None:
            return False

        new_head = MemoryEntityHead(
            user_id=user_id,
            entity_id=entity_id,
            current_edition_id=new_edition_id,
            version=1
        )
        db.add(new_head)
        try:
            await db.flush()
            return True
        except Exception:
            # Integrity error or other issues
            return False
    else:
        # Update existing
        stmt = update(MemoryEntityHead).where(
            MemoryEntityHead.user_id == user_id,
            MemoryEntityHead.entity_id == entity_id,
            MemoryEntityHead.version == expected_version
        ).values(
            current_edition_id=new_edition_id,
            version=expected_version + 1
        )
        result = await db.execute(stmt)
        return result.rowcount > 0


async def next_edition_seq(db: AsyncSession, user_id: int, entity_id: str) -> int:
    """
    Returns the next linear edition sequence number for this entity.
    Queries max(edition_seq) from memory_record_meta where entity_id matches.
    """
    stmt = select(func.max(MemoryRecordMeta.edition_seq)).where(
        MemoryRecordMeta.user_id == user_id,
        MemoryRecordMeta.entity_id == entity_id
    )
    result = await db.execute(stmt)
    max_seq = result.scalar_first()
    if max_seq is None:
        return 1
    return max_seq + 1
