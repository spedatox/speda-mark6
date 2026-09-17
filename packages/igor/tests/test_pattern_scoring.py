# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

from dataclasses import dataclass
from datetime import datetime, timezone

from app.services.pattern_scoring import lifecycle_status, score_evidence


@dataclass
class Evidence:
    role: str
    weight: float
    source_group: str
    observed_at: datetime = datetime.now(timezone.utc)


def test_repetition_from_one_source_cannot_manufacture_high_confidence():
    score = score_evidence([
        Evidence("support", 1.0, "exam:one") for _ in range(12)
    ])
    assert score.independent_source_count == 1
    assert score.confidence < 0.80
    assert lifecycle_status(score) == "candidate"


def test_three_supports_across_two_sources_are_eligible_for_active():
    score = score_evidence([
        Evidence("support", 1.0, "exam:one"),
        Evidence("support", 1.0, "exam:two"),
        Evidence("support", 1.0, "exam:two"),
    ])
    assert lifecycle_status(score) == "active"


def test_substantial_contradiction_contests_a_pattern():
    score = score_evidence([
        Evidence("support", 1.0, "exam:one"),
        Evidence("support", 1.0, "exam:two"),
        Evidence("contradict", 4.0, "exam:three"),
    ])
    assert lifecycle_status(score) == "contested"


def test_terminal_lifecycle_states_are_preserved():
    score = score_evidence([Evidence("support", 1.0, "exam:one")])
    assert lifecycle_status(score, current_status="retired") == "retired"
    assert lifecycle_status(score, current_status="rejected") == "rejected"
