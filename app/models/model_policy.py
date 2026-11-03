from datetime import datetime

from sqlalchemy import ForeignKey, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ModelPolicyState(Base):
    __tablename__ = "model_policy_state"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    revision: Mapped[int] = mapped_column(default=1)
    document: Mapped[dict] = mapped_column(JSON)


class ToolExecutionReceipt(Base):
    __tablename__ = "tool_execution_receipts"
    __table_args__ = (UniqueConstraint("user_id", "request_id", "call_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    request_id: Mapped[str] = mapped_column(String(64))
    call_id: Mapped[str] = mapped_column(String(256))
    name: Mapped[str] = mapped_column(String(256))
    arguments_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="started")
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
