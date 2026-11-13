"""Durable external operation bindings and resumable tool boundaries."""
from sqlalchemy import ForeignKey, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class ToolBoundary(Base):
    __tablename__ = 'tool_boundaries'
    step_id: Mapped[str] = mapped_column(ForeignKey('coord_steps.id'), primary_key=True)
    generation: Mapped[int] = mapped_column(default=0)
    snapshot: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), default='running')


class OperationBinding(Base):
    __tablename__ = 'operation_bindings'
    __table_args__ = (UniqueConstraint('executor', 'operation_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    executor: Mapped[str] = mapped_column(String(64))
    operation_id: Mapped[str] = mapped_column(String(32))
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    run_id: Mapped[str] = mapped_column(ForeignKey('coord_runs.id'))
    step_id: Mapped[str] = mapped_column(ForeignKey('coord_steps.id'))
    receipt_id: Mapped[int] = mapped_column(ForeignKey('tool_execution_receipts.id'), unique=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(24), default='prepared')
    version: Mapped[int] = mapped_column(default=0)
    result_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    deadline: Mapped[float | None] = mapped_column(nullable=True)
    cancellation: Mapped[str | None] = mapped_column(String(32), nullable=True)


class ExecutorInbox(Base):
    __tablename__ = 'executor_event_inbox'
    __table_args__ = (UniqueConstraint('executor', 'sequence'),)
    executor: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    sequence: Mapped[int] = mapped_column()
    payload_hash: Mapped[str] = mapped_column(String(64))
    disposition: Mapped[str] = mapped_column(String(24))


class ExecutorCursor(Base):
    __tablename__ = 'executor_event_cursors'
    executor: Mapped[str] = mapped_column(String(64), primary_key=True)
    sequence: Mapped[int] = mapped_column(default=0)


class OperationContinuation(Base):
    __tablename__ = 'operation_continuations'
    step_id: Mapped[str] = mapped_column(ForeignKey('coord_steps.id'), primary_key=True)
    generation: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(24), default='ready')
    fence: Mapped[int] = mapped_column(default=0)
    lease_until: Mapped[float] = mapped_column(default=0)


class OperationWaiter(Base):
    __tablename__ = 'operation_waiters'
    receipt_id: Mapped[int] = mapped_column(ForeignKey('tool_execution_receipts.id'), primary_key=True)
    binding_id: Mapped[int] = mapped_column(ForeignKey('operation_bindings.id'))
    step_id: Mapped[str] = mapped_column(ForeignKey('coord_steps.id'))


class ToolBoundaryHistory(Base):
    __tablename__ = 'tool_boundary_history'
    step_id: Mapped[str] = mapped_column(ForeignKey('coord_steps.id'), primary_key=True)
    generation: Mapped[int] = mapped_column(primary_key=True)
    snapshot: Mapped[dict] = mapped_column(JSON)
