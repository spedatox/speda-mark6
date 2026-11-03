"""Transport identity and receipts. These records do not own conversations."""
from datetime import datetime
from sqlalchemy import ForeignKey,String,JSON,UniqueConstraint,BigInteger
from sqlalchemy.orm import Mapped,mapped_column
from app.database import Base


class TelegramOwner(Base):
    __tablename__='native_telegram_owners'
    user_id:Mapped[int]=mapped_column(ForeignKey('users.id'),primary_key=True)
    sender_id:Mapped[int]=mapped_column(BigInteger,unique=True)


class TelegramLink(Base):
    __tablename__='native_telegram_links'
    user_id:Mapped[int]=mapped_column(ForeignKey('users.id'),primary_key=True)
    agent_id:Mapped[str]=mapped_column(String(64),primary_key=True)
    nonce_hash:Mapped[str|None]=mapped_column(String(64),default=None)
    nonce_expires:Mapped[float]=mapped_column(default=0)
    chat_id:Mapped[int|None]=mapped_column(BigInteger,default=None)
    room_id:Mapped[str|None]=mapped_column(ForeignKey('coord_rooms.id'),default=None)
    version:Mapped[int]=mapped_column(default=1)


class TelegramInput(Base):
    __tablename__='native_telegram_inputs'
    agent_id:Mapped[str]=mapped_column(String(64),primary_key=True)
    update_id:Mapped[int]=mapped_column(BigInteger,primary_key=True)
    user_id:Mapped[int]=mapped_column(ForeignKey('users.id'))
    request_hash:Mapped[str]=mapped_column(String(64))
    room_id:Mapped[str]=mapped_column(ForeignKey('coord_rooms.id'))
    text:Mapped[str]=mapped_column()
    model:Mapped[str|None]=mapped_column(String(255),default=None)
    run_id:Mapped[str|None]=mapped_column(ForeignKey('coord_runs.id'),default=None)


class Delivery(Base):
    __tablename__='native_deliveries'
    __table_args__=(UniqueConstraint('user_id','dedupe_key'),)
    id:Mapped[str]=mapped_column(String(32),primary_key=True)
    user_id:Mapped[int]=mapped_column(ForeignKey('users.id'),index=True)
    run_id:Mapped[str|None]=mapped_column(ForeignKey('coord_runs.id'),index=True,default=None)
    dedupe_key:Mapped[str]=mapped_column(String(64))
    agent_id:Mapped[str]=mapped_column(String(64))
    channel:Mapped[str]=mapped_column(String(16))
    payload:Mapped[dict]=mapped_column(JSON)
    status:Mapped[str]=mapped_column(String(24),default='pending',index=True)
    remote_id:Mapped[str|None]=mapped_column(String(160),default=None)
    detail:Mapped[str|None]=mapped_column(String(300),default=None)
    created_at:Mapped[datetime]=mapped_column(default=datetime.utcnow)
    updated_at:Mapped[datetime]=mapped_column(default=datetime.utcnow)
