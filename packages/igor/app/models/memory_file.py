from datetime import datetime, timezone

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MemoryFile(Base):
    """
    Virtual filesystem for SPEDA's long-term memory.

    Each row is a file in the /memories/{user_id}/ directory.
    The model reads and writes these via the memory tool during conversations —
    following Anthropic's Agent Memory architecture where the agent controls
    its own persistent context rather than having facts injected passively.
    """

    __tablename__ = "memory_files"
    __table_args__ = (
        UniqueConstraint("user_id", "path", name="uq_memory_file_user_path"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    path: Mapped[str] = mapped_column(String(512))   # e.g. /memories/owner.md
    content: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
