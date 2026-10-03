# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ChannelSession(Base):
    """Durable conversation selected for a channel without client session IDs.

    The conversation keeps its original channel; a Telegram delivery can select
    an app/n8n conversation without relabelling its transcript. A NULL session
    records an explicit reset or deletion, so legacy adoption cannot undo /new.
    """

    __tablename__ = "channel_sessions"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel: Mapped[str] = mapped_column(String(16), primary_key=True)
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True,
    )
