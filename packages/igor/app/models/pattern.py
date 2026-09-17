# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Persistent state for the Adaptive Countermeasure Engine.

The natural-language pattern claim remains an inductive :class:`Observation`.
These tables contain the machine state around that claim: scored evidence,
lifecycle, reusable responses and the outcomes of applying those responses.
"""

from datetime import datetime, timezone

from sqlalchemy import Float, ForeignKey, Index, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PatternState(Base):
    __tablename__ = "pattern_states"
    __table_args__ = (
        Index("ix_pattern_states_user_status", "user_id", "status", "confidence_score"),
        Index("ix_pattern_states_scope", "user_id", "namespace", "scope_type", "scope_key"),
    )

    # One-to-one with the canonical inductive observation.
    observation_id: Mapped[int] = mapped_column(
        ForeignKey("observations.id"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    namespace: Mapped[str] = mapped_column(String(128), default="general.tendency")
    scope_type: Mapped[str] = mapped_column(String(32), default="owner")
    scope_key: Mapped[str] = mapped_column(String(160), default="owner")

    status: Mapped[str] = mapped_column(String(16), default="candidate")
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    support_count: Mapped[int] = mapped_column(default=0)
    contradiction_count: Mapped[int] = mapped_column(default=0)
    independent_source_count: Mapped[int] = mapped_column(default=0)

    first_seen_at: Mapped[datetime] = mapped_column(default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(default=_now)
    last_evaluated_at: Mapped[datetime] = mapped_column(default=_now)
    half_life_days: Mapped[int | None] = mapped_column(nullable=True)

    feature_json: Mapped[dict] = mapped_column(JSON, default=dict)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(String(64), default="ace")
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)


class PatternEvidence(Base):
    __tablename__ = "pattern_evidence"
    __table_args__ = (
        UniqueConstraint(
            "pattern_observation_id", "evidence_kind", "evidence_ref", "role",
            name="uq_pattern_evidence_ref_role",
        ),
        Index("ix_pattern_evidence_pattern", "pattern_observation_id", "role"),
        Index("ix_pattern_evidence_source_group", "source_group"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    pattern_observation_id: Mapped[int] = mapped_column(ForeignKey("observations.id"))

    evidence_kind: Mapped[str] = mapped_column(String(32))
    evidence_ref: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="support")
    source_group: Mapped[str] = mapped_column(String(255))
    trust_class: Mapped[str] = mapped_column(String(32), default="owner_statement")
    weight: Mapped[float] = mapped_column(Float, default=1.0)

    excerpt: Mapped[str] = mapped_column(Text, default="")
    locator: Mapped[dict] = mapped_column(JSON, default=dict)
    source_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(default=_now)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class Countermeasure(Base):
    __tablename__ = "countermeasures"
    __table_args__ = (
        Index("ix_countermeasures_user_status", "user_id", "status", "effectiveness_score"),
        Index("ix_countermeasures_namespace", "user_id", "namespace"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    namespace: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(200))
    strategy: Mapped[str] = mapped_column(Text)
    trigger_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="candidate")
    autonomy_level: Mapped[str] = mapped_column(String(32), default="observe")
    effectiveness_score: Mapped[float] = mapped_column(Float, default=0.5)
    attempt_count: Mapped[int] = mapped_column(default=0)
    positive_count: Mapped[int] = mapped_column(default=0)
    negative_count: Mapped[int] = mapped_column(default=0)
    partial_count: Mapped[int] = mapped_column(default=0)
    created_by: Mapped[str] = mapped_column(String(64), default="ace")
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)


class CountermeasurePattern(Base):
    __tablename__ = "countermeasure_patterns"
    __table_args__ = (
        UniqueConstraint(
            "countermeasure_id", "pattern_observation_id",
            name="uq_countermeasure_pattern",
        ),
        Index("ix_countermeasure_patterns_pattern", "pattern_observation_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    countermeasure_id: Mapped[int] = mapped_column(ForeignKey("countermeasures.id"))
    pattern_observation_id: Mapped[int] = mapped_column(ForeignKey("observations.id"))


class CountermeasureRun(Base):
    __tablename__ = "countermeasure_runs"
    __table_args__ = (
        Index("ix_countermeasure_runs_countermeasure", "countermeasure_id", "started_at"),
        Index("ix_countermeasure_runs_request", "request_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    countermeasure_id: Mapped[int] = mapped_column(ForeignKey("countermeasures.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    agent_id: Mapped[str] = mapped_column(String(64))
    session_id: Mapped[int | None] = mapped_column(ForeignKey("sessions.id"), nullable=True)
    request_id: Mapped[str] = mapped_column(String(64), default="")
    trigger_patterns: Mapped[list] = mapped_column(JSON, default=list)
    context_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    tool_call_ids: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="running")
    outcome: Mapped[str] = mapped_column(String(16), default="unknown")
    outcome_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    outcome_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    started_at: Mapped[datetime] = mapped_column(default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
