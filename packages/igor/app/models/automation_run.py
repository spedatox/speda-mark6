# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Automation run history — one row per firing.

`Automation` (models/automation.py) only ever carries a single `last_fired_at`
timestamp, overwritten on every fire — it can say a briefing ran, never what
happened when it did. This is the ledger that answers "did last week's run
actually go through, and what did it report", the same job `ReminderCycle`
(models/reminder.py) does for reminder asks: one append-only row per event,
kept around rather than deleted, because that question is worth being able to
answer later.
"""

from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AutomationRun(Base):
    """One firing of one automation — written once, on settle, regardless of
    whether it pushed, spoke, or stayed silent."""

    __tablename__ = "automation_runs"
    __table_args__ = (
        Index("ix_automation_runs_automation_fired", "automation_id", "fired_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    automation_id: Mapped[int] = mapped_column(ForeignKey("automations.id"), index=True)

    # ok | failed | cancelled — turn_runner.py's own status values, carried
    # through unchanged rather than remapped into a second vocabulary.
    status: Mapped[str] = mapped_column(String(16))

    # Whether this firing actually reached the owner, and how.
    delivered: Mapped[bool] = mapped_column(default=False)
    channel: Mapped[str] = mapped_column(String(16), default="")  # text | voice | silent

    # The turn's closing answer — what a push actually said, or what a silent/
    # proactive_ask run would have said had it pushed. Empty when the turn
    # produced no closing text.
    report: Mapped[str] = mapped_column(Text, default="")

    request_id: Mapped[str] = mapped_column(String(64), default="")
    session_id: Mapped[int | None] = mapped_column(ForeignKey("sessions.id"), nullable=True)

    fired_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc), index=True
    )
