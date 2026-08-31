# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class BackgroundJob(Base):
    """
    A durable record of post-turn work that must happen even if this process does not.

    Until now every post-turn task was fire-and-forget: `BackgroundTasks` on the
    request path, plain `asyncio.gather(..., return_exceptions=True)` on the
    detached turn-runner path. Both have the same hole — if the task raises, or
    the container is restarted between the turn committing and the task running,
    the work is gone and nothing knows. `return_exceptions=True` does not even
    log the failure; it collects it into a list nobody reads.

    Most of the tasks self-heal by design (the recap advances a watermark,
    compaction re-checks its threshold, embedding backfills whatever is pending),
    which is why this was survivable. Two do not: the session-log line and the
    title are written once and lost forever if that one attempt fails.

    This table closes the hole without adding a scheduler. CLAUDE.md is explicit
    that n8n owns *when* things happen, and it still does — this owns *whether
    they happened*, which is a different question. The queue records the
    intention; a drain executes it; n8n and process startup drain the leftovers.
    """

    __tablename__ = "background_jobs"
    __table_args__ = (
        # The drain's hot path: due, unfinished work in creation order.
        Index("ix_background_jobs_due", "status", "run_after"),
        # Deduplication of an already-queued unit of work.
        Index("ix_background_jobs_dedup", "session_id", "kind", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("sessions.id"), nullable=True
    )

    # Handler key — see HANDLERS in app/services/task_queue.py.
    kind: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)

    # pending → running → done | failed
    #
    # `failed` is terminal and means "gave up after MAX_ATTEMPTS", not "errored
    # once": a job that errors goes back to pending with a later run_after. Rows
    # are kept after completion rather than deleted, because "did the audit
    # actually run last night?" is a question worth being able to answer.
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")

    # Earliest time this job may be claimed. Backoff moves it forward; it is not
    # a schedule — nothing fires at run_after, it only stops a failing job from
    # being retried on the very next drain.
    run_after: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc), index=True
    )
    # When the current attempt claimed the row, so a claim orphaned by a crash
    # can be told apart from one still legitimately running.
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)

    request_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
