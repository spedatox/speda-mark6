from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MemoryRevision(Base):
    """
    Append-only audit trail for every write to the /memories virtual filesystem.

    One row is recorded on EVERY mutation, no matter the author: the memory skill
    (an agent writing mid-conversation), Orion's nightly audit, and owner commits
    from the systems board all land here. This is what makes memory recoverable
    and accountable — it answers "who changed this, when, and what did it say
    before?" without depending on prose timestamps inside the files themselves.

    Nothing here is ever updated or deleted; a rollback is a NEW forward revision
    that restores older content, never an edit of history.
    """

    __tablename__ = "memory_revisions"
    __table_args__ = (
        Index("ix_memory_revision_user_path", "user_id", "path", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    path: Mapped[str] = mapped_column(String(512))
    # "owner" for a systems-board commit, "orion" for the custodian's audit, or
    # any agent_id ("speda", "atomix", …) for an in-conversation write.
    author: Mapped[str] = mapped_column(String(64))
    # The mutation kind, mirroring the memory tool commands + owner/system paths:
    # create | str_replace | insert | delete | commit | restore | audit
    action: Mapped[str] = mapped_column(String(32))
    before: Mapped[str] = mapped_column(Text, default="")   # full content pre-write ("" for create)
    after: Mapped[str] = mapped_column(Text, default="")    # full content post-write ("" for delete)
    request_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc),
    )
