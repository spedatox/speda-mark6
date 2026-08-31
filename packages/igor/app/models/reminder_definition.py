# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Reminder definitions — the ones the owner writes, edited from the app.

Cycles (models/reminder.py) are *runs*; these are the standing instructions that
produce them. They live here rather than in the n8n config node for one reason:
the owner asked to view, add and edit reminders from the clients, and a phone
cannot sensibly edit a JavaScript array inside a workflow node.

The n8n list still works — `tick` merges whatever the workflow sends with what
is stored here — so an existing fork keeps firing and nothing had to be migrated
on the day this landed. When both define the same id, this table wins: it is the
surface the owner is actually looking at.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ReminderDefinition(Base):
    __tablename__ = "reminder_definitions"
    __table_args__ = (Index("ix_reminder_definitions_agent", "agent_id", "enabled"),)

    # Slug, owner-chosen and stable — it is the join key for history, so
    # renaming one starts a fresh reminder and orphans what came before.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(32), default="speda")

    # Sent verbatim on every ask. No model rewrites it.
    text: Mapped[str] = mapped_column(Text)
    # "HH:MM" wall clock in the owner's timezone; empty = due whenever the tick runs.
    at: Mapped[str] = mapped_column(String(5), default="")
    # "*" or cron-style weekday numbers, 1=Monday … 7=Sunday.
    days: Mapped[str] = mapped_column(String(32), default="*")

    # JSON [{label, value}] — the answer buttons.
    options_json: Mapped[str] = mapped_column(Text, default="")
    every_minutes: Mapped[int] = mapped_column(Integer, default=5)
    max_asks: Mapped[int] = mapped_column(Integer, default=10)

    # Off keeps the definition and its history while stopping it from firing —
    # the thing you want at 3am, rather than deleting and losing the record.
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
