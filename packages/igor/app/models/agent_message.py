# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

from datetime import datetime

from sqlalchemy import Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AgentMessage(Base):
    """
    One inter-agent exchange: an agent (or the House Party broadcast) dispatched
    a task to another agent and got a result back. Written by
    app/core/dispatch.py and read by GET /agents/comms for the comms tray in the
    UI. Rows are append-only telemetry — they are never load-bearing for the
    dispatch itself, so a failed write must never fail a dispatch.
    """

    __tablename__ = "agent_messages"
    __table_args__ = (
        Index("ix_agent_messages_created", "created_at"),
        # The room feed (GET /agents/comms?session_id=) reads by origin, newest
        # first — without this index every war-room poll is a full scan.
        Index("ix_agent_messages_origin", "origin_session_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[str] = mapped_column(String(36))          # root request that spawned it
    from_agent: Mapped[str] = mapped_column(String(64))
    to_agent: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16), default="dispatch")    # dispatch | broadcast
    protocol: Mapped[str] = mapped_column(String(24), default="direct")  # direct | house_party
    task: Mapped[str] = mapped_column(Text)
    result: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    # Live:      running
    # Finished:  ok | error | timeout | offline | refused
    # Never finished: cancelled (the caller's turn was aborted / shutdown cancelled
    # it) | interrupted (the process died mid-dispatch; swept at next startup by
    # dispatch.sweep_stale_dispatches). A row must never be left on "running"
    # after the run that owns it is gone — that is what made the comms tray show
    # tasks "working" for weeks.
    status: Mapped[str] = mapped_column(String(16), default="running")
    duration_ms: Mapped[int | None] = mapped_column(nullable=True, default=None)
    session_id: Mapped[int | None] = mapped_column(nullable=True, default=None)  # target's session
    # The ROOM this exchange belongs to: the chat session the dispatch chain was
    # ordered from (the war room, or any agent's own chat). Propagated down the
    # whole cascade via AgentContext.extra["room_session_id"], so a sub-dispatch
    # two hops deep still surfaces in the room the owner is watching.
    origin_session_id: Mapped[int | None] = mapped_column(nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
