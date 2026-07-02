from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Index, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MessageEmbedding(Base):
    """
    One L2-normalized embedding vector per indexed message, for semantic recall
    (app/skills/semantic_search.py). session_id/user_id/agent_id are denormalized
    off Message/Session so recall can filter without a join. Searched via
    brute-force numpy dot product (see app/services/embeddings.py for why no
    vector DB is used) — single-user scale makes that the efficient choice.
    """

    __tablename__ = "message_embeddings"
    __table_args__ = (
        UniqueConstraint("message_id", name="uq_message_embedding_message"),
        Index("ix_message_embeddings_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"))
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    agent_id: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(32))
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
