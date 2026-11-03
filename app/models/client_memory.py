"""Client paths are presentation metadata; content and revisions belong to Iacon."""
from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class ClientMemoryPath(Base):
    __tablename__ = "client_memory_paths"
    __table_args__ = (UniqueConstraint("user_id", "path"),)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    record_id: Mapped[str] = mapped_column(ForeignKey("iacon_records.id"), primary_key=True)
    path: Mapped[str] = mapped_column(String(512))
