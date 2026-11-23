"""Operation Jarvis + ACE additive database models (PostgreSQL + SQLite parity)."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _ident() -> str:
    return uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class JarvisControl(Base):
    """Reboot-safe owner control & hierarchical budget policy for Operation Jarvis."""
    __tablename__ = "jarvis_controls"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    shadow_mode: Mapped[bool] = mapped_column(Boolean, default=True)  # Phase 0 default: observe only
    daily_microdollars: Mapped[int] = mapped_column(Integer, default=250000)  # $0.25/day hard root-tree cap
    daily_calls: Mapped[int] = mapped_column(Integer, default=20)
    daily_pushes: Mapped[int] = mapped_column(Integer, default=3)
    quiet_start_hour: Mapped[int] = mapped_column(Integer, default=22)
    quiet_end_hour: Mapped[int] = mapped_column(Integer, default=9)
    updated_at: Mapped[datetime] = mapped_column(default=_now)


class JarvisAllowance(Base):
    """Pessimistic daily hierarchical tree budget ledger for Operation Jarvis."""
    __tablename__ = "jarvis_allowances"
    __table_args__ = (UniqueConstraint("user_id", "day"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    day: Mapped[str] = mapped_column(String(10))
    calls: Mapped[int] = mapped_column(Integer, default=0)
    microdollars: Mapped[int] = mapped_column(Integer, default=0)
    pushes: Mapped[int] = mapped_column(Integer, default=0)


class JarvisGoal(Base):
    """Prospective memory ledger with 3-tier Intention Confidence."""
    __tablename__ = "jarvis_goals"
    __table_args__ = (
        Index("ix_jarvis_goals_owner_status", "user_id", "status", "intention_tier"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_ident)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(256))
    domain: Mapped[str] = mapped_column(String(48))  # academic | siberay | engineering | health | travel | comms | personal
    description: Mapped[str] = mapped_column(Text, default="")
    # confirmed_commitment | inferred_intention | speculative_interest
    intention_tier: Mapped[str] = mapped_column(String(24), default="inferred_intention")
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    # active | awaiting_confirmation | blocked | completed | paused | expired
    status: Mapped[str] = mapped_column(String(24), default="active")
    priority: Mapped[float] = mapped_column(Float, default=0.5)
    deadline_at: Mapped[datetime | None] = mapped_column(nullable=True)
    cue_rules_json: Mapped[dict] = mapped_column(JSON, default=dict)
    source_record_ids: Mapped[list] = mapped_column(JSON, default=list)
    confirmed_by_owner: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now)


class JarvisInitiative(Base):
    """Candidate, shadow, staged, or executed initiative with decoupled gate traces."""
    __tablename__ = "jarvis_initiatives"
    __table_args__ = (
        UniqueConstraint("user_id", "dedupe_key"),
        Index("ix_jarvis_init_owner_status", "user_id", "status", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_ident)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    goal_id: Mapped[str | None] = mapped_column(ForeignKey("jarvis_goals.id"), nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String(128))
    policy_scope: Mapped[str] = mapped_column(String(64), default="jarvis:global")
    assigned_agent: Mapped[str] = mapped_column(String(32), default="speda")
    tier: Mapped[str] = mapped_column(String(8), default="L0")  # L0 | L1 | L2 | L3
    downstream_irreversible: Mapped[bool] = mapped_column(Boolean, default=False)
    title: Mapped[str] = mapped_column(String(256))
    rationale: Mapped[str] = mapped_column(Text, default="")
    action_plan_json: Mapped[dict] = mapped_column(JSON, default=dict)
    artifact_paths: Mapped[list] = mapped_column(JSON, default=list)
    feature_vector_json: Mapped[dict] = mapped_column(JSON, default=dict)
    policy_mode: Mapped[str] = mapped_column(String(32), default="deterministic_threshold")
    action_score: Mapped[float] = mapped_column(Float, default=0.0)
    interruption_score: Mapped[float] = mapped_column(Float, default=0.0)
    # shadow | execute_l0 | stage_l1 | execute_l2 | await_l3 | reject
    action_decision: Mapped[str] = mapped_column(String(24), default="shadow")
    # silent_only | stage_inbox | defer_window | notify_now
    interruption_decision: Mapped[str] = mapped_column(String(24), default="silent_only")
    status: Mapped[str] = mapped_column(String(24), default="shadow")
    root_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    receipt_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    estimated_microdollars: Mapped[int] = mapped_column(Integer, default=0)
    actual_microdollars: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now)


class JarvisActionLease(Base):
    """Scoped, time-bounded delegation lease (immutable to ACE)."""
    __tablename__ = "jarvis_action_leases"
    __table_args__ = (
        Index("ix_jarvis_leases_owner", "user_id", "domain", "revoked_at"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_ident)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(128))
    domain: Mapped[str] = mapped_column(String(48))
    allowed_tools: Mapped[list] = mapped_column(JSON, default=list)
    max_tier: Mapped[str] = mapped_column(String(8), default="L2")
    daily_cap: Mapped[int] = mapped_column(Integer, default=3)
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class ACEPolicy(Base):
    """Versioned contextual online-learning policy weights for ACE."""
    __tablename__ = "ace_policies"
    __table_args__ = (
        UniqueConstraint("user_id", "policy_scope", "model_head"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_ident)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    policy_scope: Mapped[str] = mapped_column(String(64))  # e.g. jarvis:global, jarvis:academic, agent:ultron
    model_head: Mapped[str] = mapped_column(String(24))    # useful | welcome_now
    version: Mapped[int] = mapped_column(Integer, default=1)
    weights_json: Mapped[dict] = mapped_column(JSON, default=dict)
    prior_json: Mapped[dict] = mapped_column(JSON, default=dict)
    counts_json: Mapped[dict] = mapped_column(JSON, default=dict)
    explicit_locks_json: Mapped[dict] = mapped_column(JSON, default=dict)
    frozen: Mapped[bool] = mapped_column(Boolean, default=False)
    freeze_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    rolling_brier: Mapped[float | None] = mapped_column(Float, nullable=True)
    brier_window_json: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(default=_now)


class ACEPolicySnapshot(Base):
    """Immutable version history and human-readable explanations for ACE policy changes."""
    __tablename__ = "ace_policy_snapshots"
    __table_args__ = (
        UniqueConstraint("policy_id", "version"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    policy_id: Mapped[str] = mapped_column(ForeignKey("ace_policies.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    weights_json: Mapped[dict] = mapped_column(JSON, default=dict)
    delta_summary: Mapped[str] = mapped_column(Text, default="")
    trigger_event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class ACETrainingEvent(Base):
    """Atomic, idempotent, authenticated feedback observation ledger for ACE (at-most-once update)."""
    __tablename__ = "ace_training_events"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key"),
        Index("ix_ace_events_owner_init", "user_id", "initiative_id", "created_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    initiative_id: Mapped[str] = mapped_column(ForeignKey("jarvis_initiatives.id"))
    policy_scope: Mapped[str] = mapped_column(String(64))
    # explicit | outcome | behavioral | censored
    source_category: Mapped[str] = mapped_column(String(16))
    # action_usefulness | notification_timing | duplication | task_completion
    feedback_dimension: Mapped[str] = mapped_column(String(24))
    signal_name: Mapped[str] = mapped_column(String(40))
    # useful | welcome_now | none
    target_head: Mapped[str] = mapped_column(String(24))
    reward: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight: Mapped[float] = mapped_column(Float, default=0.0)
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    policy_version_after: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # owner_session | owner_telegram | system_outcome (NEVER untrusted)
    actor_auth: Mapped[str] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)
