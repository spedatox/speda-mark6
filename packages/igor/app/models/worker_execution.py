"""Operational worker journal; survives deletion of the originating chat.

Origin session and ticket IDs are historical addresses, deliberately not
cascading foreign keys. The worker journal is not owner memory.
"""
from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Index, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class WorkerExecution(Base):
    __tablename__ = "worker_executions"
    __table_args__ = (Index("worker_origin", "user_id", "agent_id", "session_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    agent_id: Mapped[str] = mapped_column(String(64))
    session_id: Mapped[int] = mapped_column()
    parent_request_id: Mapped[str] = mapped_column(String(128))
    ui_run_id: Mapped[str] = mapped_column(String(128))
    ticket_id: Mapped[int | None] = mapped_column()
    worker: Mapped[str] = mapped_column(String(64))
    backend: Mapped[str] = mapped_column(String(32))
    background: Mapped[bool] = mapped_column()
    model: Mapped[str] = mapped_column(String(255))
    workshop_project_id: Mapped[str | None] = mapped_column(String(24))
    workspace: Mapped[str | None] = mapped_column(Text)
    inputs: Mapped[list] = mapped_column(JSON, default=list)
    task: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="running")
    interrupt_requested: Mapped[bool] = mapped_column(default=False)
    last_sequence: Mapped[int] = mapped_column(default=0)
    result: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    finished_at: Mapped[datetime | None] = mapped_column()


class WorkerEvent(Base):
    __tablename__ = "worker_events"
    execution_id: Mapped[str] = mapped_column(ForeignKey("worker_executions.id"), primary_key=True)
    sequence: Mapped[int] = mapped_column(primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class WorkerInput(Base):
    __tablename__ = "worker_inputs"
    __table_args__ = (UniqueConstraint("execution_id", "id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    execution_id: Mapped[str] = mapped_column(ForeignKey("worker_executions.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    created_at: Mapped[datetime] = mapped_column(default=_now)
    boundary_at: Mapped[datetime | None] = mapped_column()


class WorkerCompletion(Base):
    """One parent report per background execution, independent of live workers.

    Message IDs are historical addresses, like the originating session. A
    deleted chat must not erase the receipt or redirect it to a different chat.
    """
    __tablename__ = "worker_completions"
    execution_id: Mapped[str] = mapped_column(ForeignKey("worker_executions.id"), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(128), unique=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    seed_message_id: Mapped[int | None] = mapped_column()
    response_message_id: Mapped[int | None] = mapped_column()
    attempts: Mapped[int] = mapped_column(default=0)
    delivery_status: Mapped[str] = mapped_column(String(24), default="pending")
    delivery_attempts: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)
