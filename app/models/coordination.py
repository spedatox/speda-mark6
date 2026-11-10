"""Additive operational records. These are not Iacon facts or replacement history."""
from datetime import datetime
from sqlalchemy import ForeignKey, Index, JSON, String, Text, UniqueConstraint, LargeBinary
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class Room(Base):
    __tablename__ = "coord_rooms"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), unique=True)
    title: Mapped[str] = mapped_column(String(255))
    lead: Mapped[str] = mapped_column(String(64))
    members: Mapped[list] = mapped_column(JSON)
    project_id: Mapped[str | None] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(default=1)
    sequence: Mapped[int] = mapped_column(default=0)
    active_run: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class RoomEvent(Base):
    __tablename__ = "coord_events"
    room_id: Mapped[str] = mapped_column(ForeignKey("coord_rooms.id"), primary_key=True)
    sequence: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str | None] = mapped_column(String(32), index=True)
    step_id: Mapped[str | None] = mapped_column(String(32))
    speaker: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(48))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class CoordinationRun(Base):
    __tablename__ = "coord_runs"
    __table_args__ = (UniqueConstraint("room_id", "input_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    room_id: Mapped[str] = mapped_column(ForeignKey("coord_rooms.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    input_id: Mapped[str] = mapped_column(String(64))
    input_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="queued")
    policy: Mapped[dict] = mapped_column(JSON)
    profiles: Mapped[dict] = mapped_column(JSON)
    limits: Mapped[dict] = mapped_column(JSON)
    memory: Mapped[dict | None] = mapped_column(JSON)
    memory_budget: Mapped[dict] = mapped_column(JSON, default=dict)
    calls: Mapped[int] = mapped_column(default=0)
    tokens: Mapped[int] = mapped_column(default=0)
    microdollars: Mapped[int] = mapped_column(default=0)
    contributions: Mapped[int] = mapped_column(default=0)
    cancelled: Mapped[bool] = mapped_column(default=False)
    deadline: Mapped[float] = mapped_column()
    executor: Mapped[str | None] = mapped_column(String(32))
    lease_until: Mapped[float] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class AgentStep(Base):
    __tablename__ = "coord_steps"
    __table_args__ = (UniqueConstraint("run_id", "operation_id"), Index("coord_step_queue", "run_id", "ordinal"))
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("coord_runs.id"))
    operation_id: Mapped[str] = mapped_column(String(128))
    parent_id: Mapped[str | None] = mapped_column(String(32))
    ordinal: Mapped[int] = mapped_column()
    agent_id: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(24), default="address")
    prompt: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    history: Mapped[list | None] = mapped_column(JSON)
    system_prompt: Mapped[str | None] = mapped_column(Text)
    result: Mapped[str | None] = mapped_column(Text)


class CoordinationControl(Base):
    __tablename__ = "coord_control"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    version: Mapped[int] = mapped_column(default=1)
    policy: Mapped[dict] = mapped_column(JSON)


class CoordinationDay(Base):
    __tablename__ = "coord_days"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    calls: Mapped[int] = mapped_column(default=0)
    tokens: Mapped[int] = mapped_column(default=0)
    microdollars: Mapped[int] = mapped_column(default=0)


class ModelCallReceipt(Base):
    __tablename__ = "coord_model_calls"
    __table_args__ = (UniqueConstraint("run_id", "call_key"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("coord_runs.id"), index=True)
    call_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="started")
    route: Mapped[dict] = mapped_column(JSON)
    tokens: Mapped[int] = mapped_column()
    microdollars: Mapped[int] = mapped_column()
    response: Mapped[dict | None] = mapped_column(JSON)
    usage: Mapped[dict] = mapped_column(JSON, default=dict)


class PersonaPreference(Base):
    __tablename__ = "coord_persona_preferences"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[int] = mapped_column(default=1)
    settings: Mapped[dict] = mapped_column(JSON)


class Project(Base):
    __tablename__ = "coord_projects"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    host_id: Mapped[str] = mapped_column(String(128))
    path: Mapped[str] = mapped_column(Text, unique=True)
    legacy_id: Mapped[str] = mapped_column(String(24))
    # Workshop remains the sole checkpoint/claim authority during this stage.
    location_version: Mapped[int] = mapped_column(default=1)


class ProjectTask(Base):
    __tablename__ = "coord_project_tasks"
    __table_args__ = (UniqueConstraint("run_id", "operation_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("coord_projects.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("coord_runs.id"), index=True)
    operation_id: Mapped[str] = mapped_column(String(128))
    parent_step: Mapped[str] = mapped_column(String(32))
    owner_agent: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(24))
    task: Mapped[str] = mapped_column(Text)
    acceptance: Mapped[list] = mapped_column(JSON)
    dependencies: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    workspace: Mapped[str | None] = mapped_column(Text)
    base_revision: Mapped[str | None] = mapped_column(String(128))
    result: Mapped[dict | None] = mapped_column(JSON)
    cancelled: Mapped[bool] = mapped_column(default=False)


class CellReceipt(Base):
    __tablename__ = "coord_cells"
    task_id: Mapped[str] = mapped_column(ForeignKey("coord_project_tasks.id"), primary_key=True)
    status: Mapped[str] = mapped_column(String(24))
    backend: Mapped[str] = mapped_column(String(24))
    handle: Mapped[str] = mapped_column(String(256))
    workspace: Mapped[str] = mapped_column(Text)


class RoomDecision(Base):
    __tablename__ = "coord_decisions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    room_id: Mapped[str] = mapped_column(ForeignKey("coord_rooms.id"), index=True)
    version: Mapped[int] = mapped_column(default=1)
    speaker: Mapped[str] = mapped_column(String(64))
    proposal: Mapped[str] = mapped_column(Text)
    evidence: Mapped[list] = mapped_column(JSON)
    objections: Mapped[list] = mapped_column(JSON)
    disposition: Mapped[str] = mapped_column(String(24))


class TaskInput(Base):
    __tablename__ = "coord_task_inputs"
    __table_args__ = (UniqueConstraint("task_id", "input_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("coord_project_tasks.id"), index=True)
    input_id: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    boundary_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class ProjectOperation(Base):
    __tablename__ = "coord_project_operations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("coord_projects.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), default="prepared")
    details: Mapped[dict] = mapped_column(JSON)


class WorkerCheck(Base):
    __tablename__ = "coord_worker_checks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("coord_project_tasks.id"), index=True)
    call_id: Mapped[str] = mapped_column(String(128))
    before_revision: Mapped[str] = mapped_column(String(64))
    after_revision: Mapped[str | None] = mapped_column(String(64))
    command: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), default="started")
    result: Mapped[dict | None] = mapped_column(JSON)


class RoomAttachment(Base):
    __tablename__ = "coord_attachments"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    room_id: Mapped[str] = mapped_column(ForeignKey("coord_rooms.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    mime: Mapped[str] = mapped_column(String(128))
    sha256: Mapped[str] = mapped_column(String(64))
    original: Mapped[bytes] = mapped_column(LargeBinary)
    extracted: Mapped[str] = mapped_column(Text)


class PdfSearchIndex(Base):
    """Disposable local text index for an immutable, access-checked PDF source."""
    __tablename__ = "coord_pdf_search_indexes"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    source_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    page_count: Mapped[int]
    indexed_pages: Mapped[int]
    pages: Mapped[list] = mapped_column(JSON)


class SupervisorInput(Base):
    __tablename__ = 'coord_supervisor_inputs'
    __table_args__ = (UniqueConstraint('step_id', 'input_id'),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    step_id: Mapped[str] = mapped_column(ForeignKey('coord_steps.id'), index=True)
    input_id: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default='queued')
    boundary_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class WorkerReview(Base):
    __tablename__ = 'coord_worker_reviews'
    task_id: Mapped[str] = mapped_column(ForeignKey('coord_project_tasks.id'), primary_key=True)
    version: Mapped[int] = mapped_column(default=1)
    reviewer_step: Mapped[str] = mapped_column(ForeignKey('coord_steps.id'))
    artifact_revision: Mapped[str] = mapped_column(String(64))
    verdict: Mapped[str] = mapped_column(String(24))
    rationale: Mapped[str] = mapped_column(Text)
    checks: Mapped[list] = mapped_column(JSON)


class SupervisorBoundary(Base):
    __tablename__ = 'coord_supervisor_boundaries'
    step_id: Mapped[str] = mapped_column(ForeignKey('coord_steps.id'), primary_key=True)
    messages: Mapped[list] = mapped_column(JSON)
    model_call_index: Mapped[int] = mapped_column()


class ForgeBoundary(Base):
    __tablename__ = 'coord_forge_boundaries'
    task_id: Mapped[str] = mapped_column(ForeignKey('coord_project_tasks.id'), primary_key=True)
    messages: Mapped[list] = mapped_column(JSON)
    continuations: Mapped[list] = mapped_column(JSON)
    model_call_index: Mapped[int] = mapped_column()
    artifact_revision: Mapped[str | None] = mapped_column(String(64))
    boundary_hash: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
