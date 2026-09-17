# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Deterministic confidence and lifecycle rules for ACE patterns."""

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable


@dataclass(frozen=True)
class PatternScore:
    confidence: float
    support_count: int
    contradiction_count: int
    independent_source_count: int
    freshness: float


def confidence_label(score: float) -> str:
    if score >= 0.80:
        return "high"
    if score >= 0.55:
        return "medium"
    return "low"


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def score_evidence(
    evidence: Iterable,
    *,
    required_diversity: int = 2,
    half_life_days: int | None = None,
    reinforcement_count: int = 1,
    now: datetime | None = None,
) -> PatternScore:
    """Score evidence without letting duplicate sources manufacture certainty.

    Repetition contributes only a small capped support bonus. The diversity
    multiplier remains decisive, so one source group can never reach high
    confidence merely by repeating itself.
    """
    rows = list(evidence)
    support_rows = [row for row in rows if row.role == "support" and row.weight > 0]
    opposing_rows = [row for row in rows if row.role == "contradict" and row.weight > 0]
    support = sum(float(row.weight) for row in support_rows)
    opposition = sum(float(row.weight) for row in opposing_rows)
    support += min(1.0, max(0, reinforcement_count - 1) * 0.10)

    posterior = (1.0 + support) / (2.0 + support + opposition)
    groups = {row.source_group for row in support_rows if row.source_group}
    diversity = min(1.0, len(groups) / max(1, required_diversity))

    current = _aware(now) or datetime.now(timezone.utc)
    timestamps = [_aware(row.observed_at) for row in support_rows]
    timestamps = [stamp for stamp in timestamps if stamp is not None]
    freshness = 1.0
    if half_life_days and timestamps:
        age_days = max(0.0, (current - max(timestamps)).total_seconds() / 86400.0)
        freshness = math.pow(0.5, age_days / max(1, half_life_days))

    return PatternScore(
        confidence=max(0.0, min(1.0, posterior * diversity * freshness)),
        support_count=len(support_rows),
        contradiction_count=len(opposing_rows),
        independent_source_count=len(groups),
        freshness=freshness,
    )


def lifecycle_status(
    score: PatternScore,
    *,
    current_status: str = "candidate",
    active_min_support: int = 3,
    active_min_diversity: int = 2,
) -> str:
    """Apply explicit lifecycle boundaries while preserving terminal states."""
    if current_status in {"retired", "rejected"}:
        return current_status
    if score.contradiction_count and (
        score.contradiction_count >= score.support_count
        or score.confidence < 0.55
    ):
        return "contested"
    if (
        score.support_count >= active_min_support
        and score.independent_source_count >= active_min_diversity
    ):
        return "active"
    return "candidate"
