# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Deterministic ACE analysis paths.

Semantic candidate generation can be added behind the same boundary later. The
first runtime path intentionally spends no model calls: it synchronizes existing
inductive observations and extracts a small, auditable set of assessment
features from project artifacts.
"""

import logging
import re
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.observation import Observation
from app.models.pattern import Countermeasure, CountermeasurePattern
from app.models.project import Project, ProjectFile
from app.services.attachments import segments_from_text
from app.services.pattern_evidence import sync_inductive_observation, sync_user_patterns

logger = logging.getLogger(__name__)

_NEGATIVE_RE = re.compile(
    r"\b(?:not|except|least|false|incorrect|isn't|aren't|değil|hariç|yanlış|yanlıştır)\b",
    re.IGNORECASE,
)
_OPTION_RE = re.compile(r"(?m)^\s*(?:[A-Ea-e][).]|\([A-Ea-e]\)|[1-5][).])\s+")

_FEATURES = {
    "negative_stem": (
        "negatively worded assessment items",
        "Assessment documents in project {project} repeatedly use negatively worded question stems.",
    ),
    "multiple_choice": (
        "multiple-choice assessment items",
        "Assessment documents in project {project} repeatedly use multiple-choice questions.",
    ),
}

_COUNTERMEASURES = {
    "negative_stem": (
        "Negation stem check",
        "Before solving, identify every negative qualifier and restate the stem as the positive condition being tested.",
        {"question_features": ["negative_stem"]},
    ),
    "multiple_choice": (
        "Distractor elimination check",
        "Before committing to an option, state why each plausible distractor fails the exact question stem.",
        {"question_features": ["multiple_choice"]},
    ),
}


async def _ensure_countermeasure(db, *, user_id: int, pattern_id: int, feature_key: str) -> None:
    title, strategy, trigger = _COUNTERMEASURES[feature_key]
    countermeasure = (
        await db.execute(
            select(Countermeasure).where(
                Countermeasure.user_id == user_id,
                Countermeasure.namespace == "academic.exam_strategy",
                Countermeasure.title == title,
            )
        )
    ).scalar_one_or_none()
    if countermeasure is None:
        countermeasure = Countermeasure(
            user_id=user_id,
            namespace="academic.exam_strategy",
            title=title,
            strategy=strategy,
            trigger_json=trigger,
            status="candidate",
            autonomy_level="adapt",
            created_by="ace:academic",
        )
        db.add(countermeasure)
        await db.flush()
    link = (
        await db.execute(
            select(CountermeasurePattern).where(
                CountermeasurePattern.countermeasure_id == countermeasure.id,
                CountermeasurePattern.pattern_observation_id == pattern_id,
            )
        )
    ).scalar_one_or_none()
    if link is None:
        db.add(CountermeasurePattern(
            countermeasure_id=countermeasure.id,
            pattern_observation_id=pattern_id,
        ))


async def analyze_patterns(*, user_id: int, request_id: str = "") -> int:
    """Idempotently bring every canonical inductive observation into ACE."""
    async with AsyncSessionLocal() as db:
        count = await sync_user_patterns(db, user_id)
    logger.info(
        "patterns_analyzed",
        extra={"request_id": request_id, "user_id": user_id, "patterns": count},
    )
    return count


def _artifact_features(content: str) -> dict[str, int]:
    segments = segments_from_text(content)
    questions = [segment.text for segment in segments if segment.item_number]
    corpus = questions or [content]
    negative = sum(1 for text in corpus if _NEGATIVE_RE.search(text))
    multiple_choice = sum(1 for text in corpus if len(_OPTION_RE.findall(text)) >= 2)
    return {
        key: value
        for key, value in {
            "negative_stem": negative,
            "multiple_choice": multiple_choice,
        }.items()
        if value > 0
    }


async def analyze_artifact(
    *, user_id: int, project_file_id: int, request_id: str = ""
) -> int:
    """Extract conservative academic features and consolidate across files.

    Each file contributes at most one support observation per feature. Ten
    questions from one exam therefore remain one independent source, while the
    same feature in three separate files can activate a pattern.
    """
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                select(ProjectFile, Project)
                .join(Project, Project.id == ProjectFile.project_id)
                .where(ProjectFile.id == project_file_id, Project.user_id == user_id)
            )
        ).first()
        if row is None:
            return 0
        artifact, project = row
        features = _artifact_features(artifact.content)
        created = 0

        for feature_key, count in features.items():
            descriptor, claim_template = _FEATURES[feature_key]
            source_content = (
                f"Artifact {artifact.id} in project {project.name} contains "
                f"{count} {descriptor}."
            )
            source = (
                await db.execute(
                    select(Observation).where(
                        Observation.user_id == user_id,
                        Observation.origin == "artifact",
                        Observation.request_id == f"artifact:{artifact.id}",
                        Observation.content == source_content,
                    )
                )
            ).scalar_one_or_none()
            if source is None:
                source = Observation(
                    user_id=user_id,
                    observer="ace:academic",
                    origin="artifact",
                    content=source_content,
                    level="explicit",
                    subject=f"project:{project.name}",
                    domain="project",
                    request_id=f"artifact:{artifact.id}",
                )
                db.add(source)
                await db.flush()
                created += 1

            # The descriptor is deliberately stable while the leading artifact
            # id stays exact provenance. Grouping is deterministic and auditable.
            all_sources = list(
                (
                    await db.execute(
                        select(Observation).where(
                            Observation.user_id == user_id,
                            Observation.origin == "artifact",
                            Observation.subject == f"project:{project.name}",
                            Observation.content.like(f"% {descriptor}."),
                            Observation.deleted_at.is_(None),
                        )
                    )
                ).scalars().all()
            )
            if len(all_sources) < 2:
                continue

            claim = claim_template.format(project=project.name)
            pattern = (
                await db.execute(
                    select(Observation).where(
                        Observation.user_id == user_id,
                        Observation.level == "inductive",
                        Observation.content == claim,
                        Observation.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            source_ids = [item.id for item in all_sources]
            source_texts = [item.content for item in all_sources]
            if pattern is None:
                pattern = Observation(
                    user_id=user_id,
                    observer="ace:academic",
                    origin="artifact",
                    content=claim,
                    level="inductive",
                    subject=f"project:{project.name}",
                    domain="project",
                    source_ids=source_ids,
                    sources=source_texts,
                    pattern_type="tendency",
                    confidence="low",
                    request_id=request_id,
                )
                db.add(pattern)
                await db.flush()
                created += 1
            else:
                pattern.source_ids = source_ids
                pattern.sources = source_texts
                pattern.updated_at = datetime.now(timezone.utc)

            state = await sync_inductive_observation(db, pattern, commit=False)
            if state is not None:
                state.namespace = "academic.exam_style"
                state.feature_json = {"mechanic": feature_key, "project_id": project.id}
                state.tags = ["academic", "assessment", feature_key]
                if state.status == "active":
                    await _ensure_countermeasure(
                        db,
                        user_id=user_id,
                        pattern_id=pattern.id,
                        feature_key=feature_key,
                    )

        await db.commit()

    logger.info(
        "artifact_analyzed",
        extra={
            "request_id": request_id,
            "project_file_id": project_file_id,
            "features": list(features),
            "observations_created": created,
        },
    )
    return created
