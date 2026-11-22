"""Iacon catalogue, immutable revisions, durable work and cost reservations."""
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import Boolean, Float, ForeignKey, Index, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


def ident():
    return uuid4().hex


def now():
    return datetime.now(timezone.utc)


class IaconRecord(Base):
    __tablename__ = "iacon_records"
    __table_args__ = (Index("ix_iacon_owner_kind", "user_id", "kind", "status"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=ident)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(24))
    title: Mapped[str] = mapped_column(String(256))
    payload: Mapped[dict] = mapped_column(JSON)
    evidence: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), default="accepted")
    version: Mapped[int] = mapped_column(default=1)
    updated_at: Mapped[datetime] = mapped_column(default=now)


class IaconLink(Base):
    __tablename__ = "iacon_links"
    __table_args__ = (UniqueConstraint("source_id", "target_id", "relation"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("iacon_records.id"), index=True)
    target_id: Mapped[str] = mapped_column(ForeignKey("iacon_records.id"), index=True)
    relation: Mapped[str] = mapped_column(String(40))


class IaconSourceRetirement(Base):
    """Read-view exclusion. The original source and its provenance stay intact."""
    __tablename__ = "iacon_source_retirements"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("memory_changes.id"), primary_key=True)
    record_id: Mapped[str] = mapped_column(ForeignKey("iacon_records.id"))
    version: Mapped[int] = mapped_column()


class IaconRevision(Base):
    __tablename__ = "iacon_revisions"
    __table_args__ = (UniqueConstraint("record_id", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    record_id: Mapped[str] = mapped_column(ForeignKey("iacon_records.id"))
    version: Mapped[int] = mapped_column()
    command_id: Mapped[str] = mapped_column(String(128))
    actor: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(default=now)


class IaconWork(Base):
    __tablename__ = "iacon_work"
    __table_args__ = (UniqueConstraint("user_id", "work_key"),
        Index("ix_iacon_work_due", "user_id", "status", "available_at"))
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=ident)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    work_key: Mapped[str] = mapped_column(String(256))
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    attempts: Mapped[int] = mapped_column(default=0)
    claim: Mapped[str | None] = mapped_column(String(32), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(String(256), nullable=True)
    available_at: Mapped[datetime] = mapped_column(default=now)
    updated_at: Mapped[datetime] = mapped_column(default=now)


class IaconControl(Base):
    __tablename__ = "iacon_controls"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    version: Mapped[int] = mapped_column(default=1)
    policy: Mapped[dict] = mapped_column(JSON)


class IaconMaintenanceRoute(Base):
    __tablename__ = "iacon_maintenance_routes"
    user_id: Mapped[int] = mapped_column(ForeignKey("iacon_controls.user_id"), primary_key=True)
    policy_revision: Mapped[int] = mapped_column()
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)


class IaconAllowance(Base):
    __tablename__ = "iacon_allowances"
    __table_args__ = (UniqueConstraint("user_id", "day"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    day: Mapped[str] = mapped_column(String(10))
    calls: Mapped[int] = mapped_column(default=0)
    tokens: Mapped[int] = mapped_column(default=0)
    microdollars: Mapped[int] = mapped_column(default=0)


class IaconCall(Base):
    __tablename__ = "iacon_calls"
    __table_args__ = (UniqueConstraint("user_id", "work_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=ident)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    work_id: Mapped[str] = mapped_column(String(256), index=True)
    day: Mapped[str] = mapped_column(String(10))
    route: Mapped[dict] = mapped_column(JSON)
    reserved_tokens: Mapped[int] = mapped_column()
    reserved_microdollars: Mapped[int] = mapped_column()
    status: Mapped[str] = mapped_column(String(24), default="reserved")
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=now)


class IaconFollowup(Base):
    __tablename__ = "iacon_followups"
    __table_args__ = (UniqueConstraint("state_id", "state_version"),
        Index("ix_iacon_followup_due", "user_id", "status", "due_at"))
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=ident)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    state_id: Mapped[str] = mapped_column(ForeignKey("iacon_records.id"))
    state_version: Mapped[int] = mapped_column()
    status: Mapped[str] = mapped_column(String(24), default="scheduled")
    due_at: Mapped[datetime] = mapped_column()
    asked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    attempts: Mapped[int] = mapped_column(default=0)
    question: Mapped[str] = mapped_column(Text)
    message_id: Mapped[int | None] = mapped_column(nullable=True)


class IaconTelegramUpdate(Base):
    __tablename__ = "iacon_telegram_updates"
    update_id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    result: Mapped[dict] = mapped_column(JSON)


class IaconEdge(Base):
    """Bi-temporal associative memory edge with epistemic mode and verbatim provenance."""
    __tablename__ = "iacon_edges"
    __table_args__ = (
        Index("ix_iacon_edges_user_active", "user_id", "status", "superseded_at"),
        Index("ix_iacon_edges_src", "user_id", "source_id"),
        Index("ix_iacon_edges_dst", "user_id", "target_id"),
        Index("ix_iacon_edges_valid", "user_id", "valid_from", "valid_until"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=ident)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("iacon_records.id"), index=True)
    target_id: Mapped[str] = mapped_column(ForeignKey("iacon_records.id"), index=True)
    relation: Mapped[str] = mapped_column(String(48))
    description: Mapped[str] = mapped_column(Text, default="")
    epistemic_mode: Mapped[str] = mapped_column(String(16), default="explicit")  # explicit | inferred
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    # Timeline T: World / Valid Time
    valid_from: Mapped[datetime | None] = mapped_column(nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(nullable=True)
    # Timeline T': Transaction / Recorded Time
    recorded_at: Mapped[datetime] = mapped_column(default=now)
    superseded_at: Mapped[datetime | None] = mapped_column(nullable=True)
    superseded_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(24), default="accepted")  # provisional | accepted | superseded | contested | deleted
    version: Mapped[int] = mapped_column(default=1)
    updated_at: Mapped[datetime] = mapped_column(default=now)


class IaconNarrative(Base):
    """Dreaming-synthesized autobiographical summary cards with deterministic dependency tracking."""
    __tablename__ = "iacon_narratives"
    __table_args__ = (
        UniqueConstraint("user_id", "slug"),
        Index("ix_iacon_narratives_user_tier", "user_id", "tier", "status"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=ident)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    tier: Mapped[str] = mapped_column(String(24))  # core_identity | active_life | chapter | entity_dossier | preferences
    slug: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(256))
    summary_markdown: Mapped[str] = mapped_column(Text)
    explicit_facts: Mapped[list] = mapped_column(JSON, default=list)
    inferred_links: Mapped[list] = mapped_column(JSON, default=list)
    source_record_ids: Mapped[list] = mapped_column(JSON, default=list)
    source_edge_ids: Mapped[list] = mapped_column(JSON, default=list)
    source_digest: Mapped[str] = mapped_column(String(64), default="")
    time_span_start: Mapped[datetime | None] = mapped_column(nullable=True)
    time_span_end: Mapped[datetime | None] = mapped_column(nullable=True)
    priority_weight: Mapped[float] = mapped_column(Float, default=1.0)
    owner_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(24), default="accepted")  # accepted | stale_deleted | deleted
    version: Mapped[int] = mapped_column(default=1)
    updated_at: Mapped[datetime] = mapped_column(default=now)


class IaconEntityAlias(Base):
    """Unicode-folded alias index for bilingual and nickname entity resolution."""
    __tablename__ = "iacon_entity_aliases"
    __table_args__ = (
        UniqueConstraint("user_id", "record_id", "alias_folded"),
        Index("ix_iacon_aliases_lookup", "user_id", "alias_folded"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    record_id: Mapped[str] = mapped_column(ForeignKey("iacon_records.id"))
    alias_folded: Mapped[str] = mapped_column(String(256))
    alias_raw: Mapped[str] = mapped_column(String(256))
    source: Mapped[str] = mapped_column(String(24), default="extracted")  # extracted | merged | owner
    created_at: Mapped[datetime] = mapped_column(default=now)
