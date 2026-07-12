from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Session(Base):
    __tablename__ = "sessions"
    # Agent-scoped listing — Sentinel's history never shows up in Ultron's list.
    __table_args__ = (
        Index("ix_sessions_user_agent_started", "user_id", "agent_id", "started_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    agent_id: Mapped[str] = mapped_column(String(64), default="speda")
    # Which surface the conversation happened on. "app" (desktop/Flutter) or
    # "telegram". Scopes the sticky-session lookup so the Telegram thread with an
    # agent and the app thread with the same agent stay separate, and lets the UI
    # badge Telegram-originated sessions. A Telegram turn is otherwise a normal
    # orchestrator turn — same tables, same memory extraction.
    channel: Mapped[str] = mapped_column(String(16), default="app")
    triggered_by: Mapped[str] = mapped_column(String(32))  # user | n8n | agent
    model_used: Mapped[str] = mapped_column(String(64))
    title: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    started_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
    ended_at: Mapped[datetime | None] = mapped_column(nullable=True, default=None)
    token_count_input: Mapped[int | None] = mapped_column(nullable=True, default=None)
    token_count_output: Mapped[int | None] = mapped_column(nullable=True, default=None)

    # ── Conversation compaction ──────────────────────────────────────────────
    # When a session's history grows large, older turns are summarized (in a
    # background task) into `summary`, and `summary_through_id` records the
    # highest message.id that summary covers. load_history then sends
    # [summary] + [messages after the watermark] instead of the full transcript,
    # capping the per-turn input cost on long conversations. NULL = not yet
    # compacted (send everything). The raw messages are never deleted — the UI
    # still shows the full history; only the model's context is compacted.
    summary: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    summary_through_id: Mapped[int | None] = mapped_column(nullable=True, default=None)

    # ── Episodic recap (cross-session carryover) ─────────────────────────────
    # Unlike `summary` (in-session compaction, long chats only), `recap` is a
    # short rolling digest of THIS session — subject, key decisions, open
    # threads — maintained by a post-turn background task on every turn. It is
    # never read back into its own session; it exists so the NEXT session with
    # this agent can be seeded with recaps of recent past sessions ("what were
    # we discussing last time?"). `recap_through_id` is the highest message.id
    # the recap covers, exactly like summary_through_id.
    recap: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    recap_through_id: Mapped[int | None] = mapped_column(nullable=True, default=None)
