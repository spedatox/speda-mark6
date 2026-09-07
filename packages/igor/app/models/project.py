# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Projects — a named workspace that owns its own chats, standing instructions
and knowledge base.

Isolation is the whole point, and it works exactly the way session isolation
does: a project carries an `agent_id`, and every listing is filtered by it, so
Sentinel's "Q3 budget" project is not visible to Ultron and its knowledge never
reaches Ultron's prompt. There is no shared/global project — one row belongs to
one agent, permanently.
"""

from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Project(Base):
    __tablename__ = "projects"
    # Agent-scoped listing, newest-activity first — the shape the project grid
    # asks for on every open.
    __table_args__ = (
        Index("ix_projects_user_agent_activity", "user_id", "agent_id", "last_activity_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    # Which agent owns this project. Never null, never "all" — see the module
    # docstring on why there is no shared project.
    agent_id: Mapped[str] = mapped_column(String(64), default="speda")

    name: Mapped[str] = mapped_column(String(200))
    # The one-line blurb under the name on the card. Purely descriptive — it is
    # NOT sent to the model; `instructions` is.
    description: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    # Standing instructions prepended to every turn in every chat in this
    # project, as its own system block. This is the field that changes behaviour.
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

    # Card identity. An emoji and a hex accent, both optional — the client falls
    # back to the agent's own accent when `color` is empty.
    icon: Mapped[str | None] = mapped_column(String(16), nullable=True, default=None)
    color: Mapped[str | None] = mapped_column(String(16), nullable=True, default=None)

    pinned: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now)
    # Bumped when a chat in the project starts or is renamed, so the grid can
    # sort by "what am I actually working on" rather than by creation date.
    last_activity_at: Mapped[datetime] = mapped_column(default=_now)
    # Archived, not deleted: the chats inside stay readable. NULL = active.
    archived_at: Mapped[datetime | None] = mapped_column(nullable=True, default=None)


class ProjectFile(Base):
    """One document in a project's knowledge base.

    The extracted TEXT is what is stored, not the original bytes — same decision
    as chat attachments (services/attachments.py): text is the one representation
    that reaches every provider identically, survives the Anthropic→chat-
    completions translation, and can be budgeted by character count before it is
    ever put in front of a model. The original file is not kept; a knowledge base
    is not a file store.
    """

    __tablename__ = "project_files"
    __table_args__ = (Index("ix_project_files_project", "project_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    name: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(128), default="")
    # Size of the ORIGINAL upload, for the chip. `chars` is the size of what the
    # model actually sees, which is the number the knowledge budget counts.
    size_bytes: Mapped[int] = mapped_column(default=0)
    chars: Mapped[int] = mapped_column(default=0)
    content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=_now)
