from datetime import datetime

from sqlalchemy import ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ToolCall(Base):
    __tablename__ = "tool_calls"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"))
    request_id: Mapped[str] = mapped_column(String(64))
    tool_name: Mapped[str] = mapped_column(String(128))
    tool_input: Mapped[dict] = mapped_column(JSON)
    tool_result: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
    duration_ms: Mapped[int | None] = mapped_column(nullable=True, default=None)
    error: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    called_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
