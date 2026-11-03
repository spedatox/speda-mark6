from datetime import datetime, timezone

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    agent_id: Mapped[str] = mapped_column(String(64), default="speda")
    triggered_by: Mapped[str] = mapped_column(String(32))  # user | n8n | agent
    model_used: Mapped[str] = mapped_column(String(64))
    title: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    started_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
    ended_at: Mapped[datetime | None] = mapped_column(nullable=True, default=None)
    token_count_input: Mapped[int | None] = mapped_column(nullable=True, default=None)
    token_count_output: Mapped[int | None] = mapped_column(nullable=True, default=None)
