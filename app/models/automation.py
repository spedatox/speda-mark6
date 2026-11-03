"""Native definition metadata and pre-effect n8n operations; n8n owns timing."""
from datetime import datetime
from sqlalchemy import ForeignKey,String,JSON,UniqueConstraint
from sqlalchemy.orm import Mapped,mapped_column
from app.database import Base


class Automation(Base):
    __tablename__='native_automations'
    __table_args__=(UniqueConstraint('user_id','request_id'),)
    id: Mapped[int]=mapped_column(primary_key=True,autoincrement=True)
    public_key: Mapped[str]=mapped_column(String(32),unique=True)
    user_id: Mapped[int]=mapped_column(ForeignKey('users.id'),index=True)
    agent_id: Mapped[str]=mapped_column(String(64))
    request_id: Mapped[str]=mapped_column(String(64))
    request_hash: Mapped[str]=mapped_column(String(64))
    name: Mapped[str]=mapped_column(String(160))
    spec: Mapped[dict]=mapped_column(JSON)
    timezone: Mapped[str]=mapped_column(String(64))
    version: Mapped[int]=mapped_column(default=1)
    content_version: Mapped[int]=mapped_column(default=1)
    applied_version: Mapped[int]=mapped_column(default=0)
    connection_binding: Mapped[dict]=mapped_column(JSON,default=dict)
    desired_active: Mapped[bool]=mapped_column(default=True)
    active: Mapped[bool]=mapped_column(default=False)
    status: Mapped[str]=mapped_column(String(32),default='pending')
    n8n_workflow_id: Mapped[str | None]=mapped_column(String(100),default=None)
    created_at: Mapped[datetime]=mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime]=mapped_column(default=datetime.utcnow)
    deleted_at: Mapped[datetime | None]=mapped_column(default=None)


class AutomationOperation(Base):
    __tablename__='native_automation_operations'
    __table_args__=(UniqueConstraint('automation_id','version'),)
    id: Mapped[str]=mapped_column(String(32),primary_key=True)
    automation_id: Mapped[int]=mapped_column(ForeignKey('native_automations.id'),index=True)
    version: Mapped[int]=mapped_column()
    action: Mapped[str]=mapped_column(String(16))
    stage: Mapped[str]=mapped_column(String(32),default='pending')
    workflow: Mapped[dict]=mapped_column(JSON)
    baseline: Mapped[dict]=mapped_column(JSON,default=dict)
    error: Mapped[str | None]=mapped_column(String(1000),default=None)
    created_at: Mapped[datetime]=mapped_column(default=datetime.utcnow)
