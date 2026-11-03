"""Webhook identities; the coordinator remains the only execution queue."""
from datetime import datetime
from sqlalchemy import ForeignKey, String, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class AutomationAdmission(Base):
    __tablename__ = 'compat_automation_admissions'
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    room_id: Mapped[str] = mapped_column(ForeignKey('coord_rooms.id'))
    agent_id: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    output_mode: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime|None] = mapped_column(default=datetime.utcnow,nullable=True)
