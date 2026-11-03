"""Client identities and presentation state, never an alternative run queue."""
from datetime import datetime
from sqlalchemy import ForeignKey, String, JSON, Text, LargeBinary, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class ClientTurn(Base):
    __tablename__ = "compat_client_turns"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    room_id: Mapped[str] = mapped_column(ForeignKey("coord_rooms.id"))
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"))
    agent_id: Mapped[str] = mapped_column(String(64))


class ClientSession(Base):
    __tablename__ = "compat_sessions"
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), primary_key=True)
    hidden: Mapped[bool] = mapped_column(default=False)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)


class HiddenMessage(Base):
    """Reversible display/context edit; source messages are never overwritten."""
    __tablename__ = 'compat_hidden_messages'
    session_id: Mapped[int] = mapped_column(ForeignKey('sessions.id'), primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey('messages.id'), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64))


class ClientReplay(Base):
    __tablename__ = 'compat_replays'
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey('messages.id'))


class ClientProject(Base):
    """Agent-private presentation/instructions for a native folder project.

    Execution, checkpoints and source claims still belong to coord_projects/Workshop.
    Removing a sidebar entry never deletes its files, chats or execution evidence.
    """
    __tablename__ = 'compat_projects'
    __table_args__ = (UniqueConstraint('user_id', 'agent_id', 'native_id'),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    agent_id: Mapped[str] = mapped_column(String(64))
    native_id: Mapped[str] = mapped_column(ForeignKey('coord_projects.id'))
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default='')
    instructions: Mapped[str] = mapped_column(Text, default='')
    icon: Mapped[str] = mapped_column(String(32), default='')
    color: Mapped[str] = mapped_column(String(32), default='')
    pinned: Mapped[bool] = mapped_column(default=False)
    archived: Mapped[bool] = mapped_column(default=False)
    removed: Mapped[bool] = mapped_column(default=False)
    version: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class ClientProjectFile(Base):
    __tablename__ = 'compat_project_files'
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey('compat_projects.id'), index=True)
    name: Mapped[str] = mapped_column(String(255))
    mime: Mapped[str] = mapped_column(String(255))
    original: Mapped[bytes] = mapped_column(LargeBinary)
    extracted: Mapped[str] = mapped_column(Text)
    removed: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
