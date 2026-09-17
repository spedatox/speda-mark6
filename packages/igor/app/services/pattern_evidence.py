# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Evidence admission and state synchronization for ACE."""

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.observation import Observation
from app.models.pattern import PatternEvidence, PatternState
from app.services.pattern_scoring import confidence_label, lifecycle_status, score_evidence

logger = logging.getLogger(__name__)

EVIDENCE_ROLES = {"support", "contradict", "context"}
TRUST_WEIGHTS = {
    "owner_statement": 1.0,
    "source_document": 1.0,
    "structured_tool": 1.0,
    "external_source": 0.8,
    # Candidates and generated material may be retained for audit/context but
    # never increase or decrease a pattern's confidence.
    "agent_inference": 0.0,
    "synthetic": 0.0,
}


def namespace_for(observation: Observation) -> str:
    domain = (observation.domain or "state").strip().lower()
    kind = (observation.pattern_type or "tendency").strip().lower()
    if observation.observer == "ultron" or domain == "training":
        prefix = "academic" if domain != "training" else "training"
    elif domain == "finance":
        prefix = "finance"
    elif domain == "project":
        prefix = "workflow"
    else:
        prefix = "owner" if observation.subject == "owner" else "general"
    return f"{prefix}.{kind}"


def scope_for(subject: str) -> tuple[str, str]:
    if subject == "owner":
        return "owner", "owner"
    scope_type, _, _name = subject.partition(":")
    return scope_type or "subject", subject


def _trust_for(observation: Observation) -> str:
    if observation.origin == "artifact":
        return "source_document"
    if observation.origin == "tool":
        return "structured_tool"
    if observation.origin in {"synthetic", "agent"}:
        return "synthetic" if observation.origin == "synthetic" else "agent_inference"
    return "owner_statement"


async def add_evidence(
    db: AsyncSession,
    *,
    pattern_observation_id: int,
    evidence_kind: str,
    evidence_ref: str,
    role: str,
    source_group: str,
    trust_class: str,
    excerpt: str = "",
    locator: dict | None = None,
    source_hash: str | None = None,
    observed_at: datetime | None = None,
    weight: float | None = None,
) -> PatternEvidence | None:
    """Admit one idempotent evidence link, enforcing poisoning rules."""
    if role not in EVIDENCE_ROLES:
        raise ValueError(f"unknown evidence role: {role}")
    if trust_class not in TRUST_WEIGHTS:
        raise ValueError(f"unknown evidence trust class: {trust_class}")
    if evidence_ref == f"observation:{pattern_observation_id}":
        return None

    existing = (
        await db.execute(
            select(PatternEvidence).where(
                PatternEvidence.pattern_observation_id == pattern_observation_id,
                PatternEvidence.evidence_kind == evidence_kind,
                PatternEvidence.evidence_ref == evidence_ref,
                PatternEvidence.role == role,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    trust_weight = TRUST_WEIGHTS[trust_class]
    row = PatternEvidence(
        pattern_observation_id=pattern_observation_id,
        evidence_kind=evidence_kind[:32],
        evidence_ref=evidence_ref[:255],
        role=role,
        source_group=(source_group or evidence_ref)[:255],
        trust_class=trust_class,
        weight=max(0.0, float(weight if weight is not None else 1.0)) * trust_weight,
        excerpt=excerpt[:2000],
        locator=locator or {},
        source_hash=source_hash,
        observed_at=observed_at or datetime.now(timezone.utc),
    )
    db.add(row)
    return row


async def recalculate_state(
    db: AsyncSession,
    state: PatternState,
    observation: Observation,
) -> PatternState:
    from app.config import settings

    evidence = list(
        (
            await db.execute(
                select(PatternEvidence).where(
                    PatternEvidence.pattern_observation_id == state.observation_id
                )
            )
        ).scalars().all()
    )
    scored = score_evidence(
        evidence,
        required_diversity=settings.ace_active_min_diversity,
        half_life_days=state.half_life_days,
        reinforcement_count=observation.reinforcement_count,
    )
    before = state.confidence_score
    state.confidence_score = scored.confidence
    state.support_count = scored.support_count
    state.contradiction_count = scored.contradiction_count
    state.independent_source_count = scored.independent_source_count
    state.status = lifecycle_status(
        scored,
        current_status=state.status,
        active_min_support=settings.ace_active_min_support,
        active_min_diversity=settings.ace_active_min_diversity,
    )
    state.last_evaluated_at = datetime.now(timezone.utc)
    state.updated_at = state.last_evaluated_at
    if evidence:
        state.last_seen_at = max(row.observed_at for row in evidence)
    observation.confidence = confidence_label(scored.confidence)
    logger.info(
        "pattern_recalculated",
        extra={
            "pattern_id": state.observation_id,
            "confidence_before": before,
            "confidence_after": state.confidence_score,
            "status": state.status,
        },
    )
    return state


async def sync_inductive_observation(
    db: AsyncSession, observation: Observation, *, commit: bool = True
) -> PatternState | None:
    """Build/update ACE state around one canonical inductive observation."""
    if observation.level != "inductive" or observation.deleted_at is not None:
        return None

    state = await db.get(PatternState, observation.id)
    if state is None:
        scope_type, scope_key = scope_for(observation.subject)
        state = PatternState(
            observation_id=observation.id,
            user_id=observation.user_id,
            namespace=namespace_for(observation),
            scope_type=scope_type,
            scope_key=scope_key,
            status="candidate",
            created_by=observation.observer,
        )
        db.add(state)
        await db.flush()
        logger.info("pattern_candidate_created", extra={"pattern_id": observation.id})

    source_rows: dict[int, Observation] = {}
    if observation.source_ids:
        sources = (
            await db.execute(
                select(Observation).where(
                    Observation.user_id == observation.user_id,
                    Observation.id.in_(observation.source_ids),
                )
            )
        ).scalars().all()
        source_rows = {row.id: row for row in sources}

    for source_id in observation.source_ids or []:
        source = source_rows.get(int(source_id))
        if source is None or source.id == observation.id:
            continue
        # Derived/model-produced claims remain context, never primary evidence.
        trust = _trust_for(source)
        if source.origin == "artifact" and source.request_id.startswith("artifact:"):
            artifact_id = source.request_id.partition(":")[2]
            group = f"project_file:{artifact_id}"
            locator = {"project_file_id": int(artifact_id)}
            from app.models.project import ProjectFile

            artifact = await db.get(ProjectFile, int(artifact_id))
            evidence_kind = "project_file"
            evidence_ref = group
            source_hash = artifact.content_hash if artifact is not None else None
        else:
            group = (
                f"session:{source.session_id}" if source.session_id is not None
                else f"observation:{source.id}"
            )
            locator = {"session_id": source.session_id, "message_ids": source.message_ids or []}
            evidence_kind = "observation"
            evidence_ref = f"observation:{source.id}"
            source_hash = None
        await add_evidence(
            db,
            pattern_observation_id=observation.id,
            evidence_kind=evidence_kind,
            evidence_ref=evidence_ref,
            role="support",
            source_group=group,
            trust_class=trust,
            excerpt=source.content,
            locator=locator,
            source_hash=source_hash,
            observed_at=source.created_at,
        )

    await db.flush()
    await recalculate_state(db, state, observation)
    if commit:
        await db.commit()
        await db.refresh(state)
    return state


async def sync_user_patterns(db: AsyncSession, user_id: int) -> int:
    """Idempotently synchronize every live inductive observation for an owner."""
    patterns = list(
        (
            await db.execute(
                select(Observation).where(
                    Observation.user_id == user_id,
                    Observation.level == "inductive",
                    Observation.deleted_at.is_(None),
                )
            )
        ).scalars().all()
    )
    for observation in patterns:
        await sync_inductive_observation(db, observation, commit=False)
    if patterns:
        await db.commit()
    return len(patterns)
