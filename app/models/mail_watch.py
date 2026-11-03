"""Immutable observed mail batches and local per-watch acknowledgements."""
from sqlalchemy import String,JSON,ForeignKey
from sqlalchemy.orm import Mapped,mapped_column
from app.database import Base


class MailBatch(Base):
    __tablename__='native_mail_batches'
    user_id:Mapped[int]=mapped_column(ForeignKey('users.id'),primary_key=True)
    fingerprint:Mapped[str]=mapped_column(String(64),primary_key=True)
    automation_id:Mapped[int]=mapped_column(ForeignKey('native_automations.id'),index=True)
    revision:Mapped[int]=mapped_column()
    provider:Mapped[str]=mapped_column(String(20))
    generation:Mapped[str]=mapped_column(String(32))
    payload:Mapped[dict]=mapped_column(JSON)
    acknowledged:Mapped[bool]=mapped_column(default=False)


class MailSeen(Base):
    __tablename__='native_mail_seen'
    user_id:Mapped[int]=mapped_column(ForeignKey('users.id'),primary_key=True)
    automation_id:Mapped[int]=mapped_column(ForeignKey('native_automations.id'),primary_key=True)
    provider:Mapped[str]=mapped_column(String(20),primary_key=True)
    generation:Mapped[str]=mapped_column(String(32),primary_key=True)
    message_id:Mapped[str]=mapped_column(String(512),primary_key=True)


class MailCursor(Base):
    __tablename__='native_mail_cursors'
    user_id:Mapped[int]=mapped_column(ForeignKey('users.id'),primary_key=True)
    automation_id:Mapped[int]=mapped_column(ForeignKey('native_automations.id'),primary_key=True)
    revision:Mapped[int]=mapped_column(primary_key=True)
    generation:Mapped[str]=mapped_column(String(32),primary_key=True)
    version:Mapped[int]=mapped_column(default=1)
    cursor:Mapped[str|None]=mapped_column(default=None)
