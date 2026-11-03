"""Durable read-worker state and presentation handles for native Forge tasks.

ProjectTask and Workshop remain the only project execution authorities. These
records are operational child work, never personal knowledge or owner assertions.
"""
from datetime import datetime
from sqlalchemy import ForeignKey, String, Text, JSON, LargeBinary, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class LegionExecution(Base):
    __tablename__ = 'coord_legion_executions'
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey('coord_runs.id'), index=True)
    parent_step: Mapped[str] = mapped_column(String(32))
    owner_agent: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(32))
    task: Mapped[str] = mapped_column(Text)
    input_hash: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(255))
    background: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(24), default='admitted')
    cancelled: Mapped[bool] = mapped_column(default=False)
    result: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    finished_at: Mapped[datetime | None]


class WorkerTicket(Base):
    __tablename__ = 'coord_worker_tickets'
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    execution_id: Mapped[str] = mapped_column(String(32), unique=True)
    kind: Mapped[str] = mapped_column(String(16))


class LegionInput(Base):
    __tablename__ = 'coord_legion_inputs'
    __table_args__ = (UniqueConstraint('execution_id', 'input_id'),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    execution_id: Mapped[str] = mapped_column(ForeignKey('coord_legion_executions.id'), index=True)
    input_id: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default='queued')
    boundary_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class WorkerEventArtifact(Base):
    __tablename__ = 'coord_worker_event_artifacts'
    execution_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    data: Mapped[bytes] = mapped_column(LargeBinary)
