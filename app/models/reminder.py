"""Owner configured reminders and deterministic question cycles; no scheduler."""
from sqlalchemy import ForeignKey,String,JSON,UniqueConstraint
from sqlalchemy.orm import Mapped,mapped_column
from app.database import Base


class ReminderDefinition(Base):
    __tablename__='native_reminder_definitions'
    user_id:Mapped[int]=mapped_column(ForeignKey('users.id'),primary_key=True)
    id:Mapped[str]=mapped_column(String(64),primary_key=True)
    version:Mapped[int]=mapped_column(default=1)
    agent_id:Mapped[str]=mapped_column(String(64))
    spec:Mapped[dict]=mapped_column(JSON)
    enabled:Mapped[bool]=mapped_column(default=True)


class ReminderCycle(Base):
    __tablename__='native_reminder_cycles'
    __table_args__=(UniqueConstraint('user_id','reminder_id','day'),)
    id:Mapped[int]=mapped_column(primary_key=True,autoincrement=True)
    user_id:Mapped[int]=mapped_column(ForeignKey('users.id'),index=True)
    reminder_id:Mapped[str]=mapped_column(String(64))
    day:Mapped[str]=mapped_column(String(10))
    agent_id:Mapped[str]=mapped_column(String(64))
    version:Mapped[int]=mapped_column(default=1)
    spec:Mapped[dict]=mapped_column(JSON)
    status:Mapped[str]=mapped_column(String(24),default='open')
    asks:Mapped[int]=mapped_column(default=0)
    next_ask_at:Mapped[float]=mapped_column(default=0)
    opened_at:Mapped[float]=mapped_column()
    closed_at:Mapped[float|None]=mapped_column(default=None)
    answer:Mapped[str|None]=mapped_column(String(64),default=None)
    answered_via:Mapped[str|None]=mapped_column(String(64),default=None)
