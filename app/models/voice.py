"""Versioned voice pins and non-replaying speech attempts."""
from sqlalchemy import ForeignKey,String,JSON
from sqlalchemy.orm import Mapped,mapped_column
from app.database import Base


class VoicePreference(Base):
    __tablename__='native_voice_preferences'
    user_id:Mapped[int]=mapped_column(ForeignKey('users.id'),primary_key=True)
    agent_id:Mapped[str]=mapped_column(String(64),primary_key=True)
    version:Mapped[int]=mapped_column(default=1)
    settings:Mapped[dict]=mapped_column(JSON,default=dict)


class SpeechAttempt(Base):
    __tablename__='native_speech_attempts'
    user_id:Mapped[int]=mapped_column(ForeignKey('users.id'),primary_key=True)
    request_id:Mapped[str]=mapped_column(String(64),primary_key=True)
    request_hash:Mapped[str]=mapped_column(String(64))
    status:Mapped[str]=mapped_column(String(24),default='admitted')
    budget_id:Mapped[str|None]=mapped_column(String(32),default=None)
    result:Mapped[dict]=mapped_column(JSON,default=dict)
