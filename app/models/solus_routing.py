"""Owner-configured bookkeeping defaults; no guessed payment accounts."""
from datetime import datetime
from sqlalchemy import ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class SolusRouting(Base):
    __tablename__ = 'solus_routing'
    book_id: Mapped[str] = mapped_column(ForeignKey('solus_books.id'), primary_key=True)
    version: Mapped[int] = mapped_column(default=1)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class SolusRoutingRevision(Base):
    __tablename__ = 'solus_routing_revisions'
    book_id: Mapped[str] = mapped_column(ForeignKey('solus_books.id'), primary_key=True)
    version: Mapped[int] = mapped_column(primary_key=True)
    settings: Mapped[dict] = mapped_column(JSON)
    evidence: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
