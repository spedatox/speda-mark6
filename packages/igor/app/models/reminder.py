"""
Persistent reminders — the ledger behind "keep asking until I answer".

One row per **ask cycle**, not per reminder definition. The definition lives in
the n8n workflow's config node (so it stays forkable and hand-editable, like the
web watch list); this table holds only what must survive a restart: is there an
open question right now, how many times has it been asked, and how did it end.

That split is deliberate. A definition edited in n8n takes effect on the next
tick with no migration, while the state that would be genuinely painful to lose
— "I already asked six times", "you answered 'taken' at 08:41" — lives in the
database. It is also what makes "did I take it on Tuesday?" answerable later:
every cycle ends as answered, gave_up or cancelled, and the row stays.
"""

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ReminderCycle(Base):
    """One run of one reminder — opened when it first comes due, closed when the
    owner answers or the ask budget runs out."""

    __tablename__ = "reminder_cycles"
    __table_args__ = (
        Index("ix_reminder_cycles_open", "reminder_id", "status"),
        Index("ix_reminder_cycles_due", "status", "next_ask_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # Stable key from the n8n config node. Renaming it there starts a new
    # reminder rather than continuing this one — same rule as watch_id.
    reminder_id: Mapped[str] = mapped_column(String(64))
    # Which agent asks. Its Telegram bot sends, and its voice is the one the
    # owner hears — a medicine question from Atomix, not from Speda.
    agent_id: Mapped[str] = mapped_column(String(32), default="speda")

    # The question as sent, kept verbatim so the transcript is honest even if
    # the wording in n8n changes mid-cycle.
    question: Mapped[str] = mapped_column(Text, default="")

    # open | answered | gave_up | cancelled
    status: Mapped[str] = mapped_column(String(16), default="open")

    # The `value` of the option the owner chose ("taken", "skipped", …), or the
    # free-text answer an agent recorded on their behalf.
    answer: Mapped[str] = mapped_column(String(64), default="")
    answered_via: Mapped[str] = mapped_column(String(16), default="")  # button | chat

    asks: Mapped[int] = mapped_column(Integer, default=0)
    max_asks: Mapped[int] = mapped_column(Integer, default=10)

    # Everything needed to RE-ask without the n8n definition in hand.
    #
    # A reminder can be opened two ways: declaratively from the n8n list (static
    # text, the cheap path), or by an agent that just composed a personalised
    # message. The second kind has no entry in any config node, so the re-ask
    # would have nothing to send unless the cycle carries its own options and
    # cadence. That is what these two columns are for — they turn a cycle into
    # a self-contained thing the tick can keep asking on its own.
    options_json: Mapped[str] = mapped_column(Text, default="")
    every_minutes: Mapped[int] = mapped_column(Integer, default=5)

    # Wall-clock date the cycle belongs to ("2026-07-28"), so a daily reminder
    # opens exactly once per day no matter how often the tick runs.
    day: Mapped[str] = mapped_column(String(10), default="")

    opened_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_ask_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_ask_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Telegram message id of the most recent ask, so the buttons on the old
    # message can be cleared once answered — an answered question must not keep
    # offering buttons that no longer do anything.
    last_message_id: Mapped[str] = mapped_column(String(32), default="")
    chat_id: Mapped[str] = mapped_column(String(32), default="")
