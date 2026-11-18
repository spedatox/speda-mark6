from datetime import datetime

from sqlalchemy import ForeignKey, Index, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_archive_session", "session_id", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"))
    role: Mapped[str] = mapped_column(String(32))  # user | assistant | tool_result
    content: Mapped[dict] = mapped_column(JSON)    # Full Anthropic content block array
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
