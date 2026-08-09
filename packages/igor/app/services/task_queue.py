"""
Durable post-turn work — the queue half of Honcho's API/worker split.

Honcho separates enqueueing from execution: the API returns immediately and a
long-lived deriver process consumes persisted queue items, which is what makes
its background work survive a restart. Mark VI cannot copy the process half —
Rule 6 puts everything on one event loop and one `app.state`, and a second
process would need its own deployment, its own health check and its own share of
a Contabo box. But the *durability* half is separable from the *process* half,
and it is the half that was actually missing.

So: the same event loop still does the work, but the intention to do it is
committed to the database first. Three drains cover the three ways work is lost:

  - **inline**, right after the turn — the normal path, same latency as before;
  - **on startup** — claims orphaned by a container restart mid-job;
  - **on demand** via `POST /admin/tasks/drain` — n8n's nightly sweep, which
    picks up anything that failed every inline retry.

This is not a scheduler and must not become one (CLAUDE.md: "Do not add internal
scheduling logic. n8n handles all of that."). Nothing here fires at a time.
`run_after` only prevents a failing job from being retried on the very next
drain; something external always has to come along and ask.

**Idempotence is the contract, not an aspiration.** A job may run twice — a
crash between the work committing and the row being marked done guarantees it
eventually will. Every handler registered below is safe to re-run: they either
advance a watermark, self-guard on a threshold, or check for their own prior
output first. Do not register a handler that is not.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.background_job import BackgroundJob

logger = logging.getLogger(__name__)

# Attempts before a job is parked as `failed`. Three is enough to ride out a
# provider blip or a locked database; past that the cause is not transient and
# retrying forever would just bury the real error in log noise.
MAX_ATTEMPTS = 3

# Backoff before a failed attempt may be reclaimed. Minutes, not seconds: the
# common failure is an LLM provider being unavailable, and hammering it again
# immediately helps nobody.
BACKOFF_MINUTES = (2, 15, 60)

# A claim older than this is presumed orphaned by a crash and returned to the
# queue. Comfortably longer than the slowest handler (Orion's audit is a
# multi-pass turn, but that runs through the trigger path, not here).
STALE_CLAIM_MINUTES = 30

# Ceiling on one drain, so a backlog is worked through over several drains
# instead of blocking one request for minutes.
DEFAULT_DRAIN_LIMIT = 32


# ── Handler registry ──────────────────────────────────────────────────────────
# kind → async fn(session_id, request_id, user_id, model). Every post-turn task
# already shares that signature, so the payload stays uniform and the registry
# stays a plain dict rather than a serialization format.

def _handlers() -> dict:
    """Built lazily: importing these at module scope would make the task queue a
    dependency of everything they import, and several of them import back."""
    from app.services.compaction import maybe_compact_session
    from app.services.embedding_indexer import embed_session_tail
    from app.services.memory import (
        generate_title,
        run_daily_maintenance,
        update_session_log,
        update_session_recap,
    )
    from app.services.observations import embed_pending_observations

    async def _title(session_id, request_id, user_id, model):
        await generate_title(session_id, request_id, model)

    async def _embed_tail(session_id, request_id, user_id, model):
        await embed_session_tail(session_id, request_id, user_id)

    async def _embed_observations(session_id, request_id, user_id, model):
        await embed_pending_observations(user_id, request_id)

    async def _compact(session_id, request_id, user_id, model):
        await maybe_compact_session(session_id, request_id, model)

    async def _render_surfaces(session_id, request_id, user_id, model):
        """Regenerate the derived memory files if the record moved this turn.

        Pure assembly, no model call, and a no-op when nothing changed — so this
        is cheap enough to run every turn, which is what keeps the injected
        memory block agreeing with the record it came from within the same
        conversation rather than only after the nightly audit.
        """
        from app.database import AsyncSessionLocal
        from app.services.memory_render import commit_rendered

        async with AsyncSessionLocal() as db:
            await commit_rendered(db, user_id, request_id=request_id, author="render")

    async def _memory_reindex(session_id, request_id, user_id, model):
        """Rebuild the memory record from raw history.

        Not post-turn work — it is enqueued by the owner pressing a button, and
        it lives on this queue for the status and retry machinery rather than the
        scheduling: a rebuild that dies with the container should be visible
        afterwards, not silently absent.
        """
        from app.services.memory_reindex import reindex

        await reindex(user_id, model, request_id=request_id)

    return {
        "session_log": update_session_log,
        "session_recap": update_session_recap,
        "daily_maintenance": run_daily_maintenance,
        "title": _title,
        "compaction": _compact,
        "embed_tail": _embed_tail,
        "embed_observations": _embed_observations,
        "render_surfaces": _render_surfaces,
        "memory_reindex": _memory_reindex,
    }


# The full post-turn set, in the order they are enqueued. Order is cosmetic —
# they are independent — but a stable order makes the queue readable.
POST_TURN_KINDS: tuple[str, ...] = (
    "session_log",
    "session_recap",
    "daily_maintenance",
    "title",
    "compaction",
    "embed_tail",
    "embed_observations",
    "render_surfaces",
)


# ── Enqueue ───────────────────────────────────────────────────────────────────

async def enqueue_one(
    *, kind: str, user_id: int, model: str, request_id: str = ""
) -> int | None:
    """Queue a single named job that is not part of the post-turn set.

    Returns the job id, or None if an identical job is already pending or
    running — pressing a long-running button twice should not start it twice.
    """
    async with AsyncSessionLocal() as db:
        existing = (
            await db.execute(
                select(BackgroundJob).where(
                    BackgroundJob.user_id == user_id,
                    BackgroundJob.kind == kind,
                    BackgroundJob.status.in_(("pending", "running")),
                )
            )
        ).scalars().first()
        if existing is not None:
            return None
        job = BackgroundJob(
            user_id=user_id,
            kind=kind,
            payload={"model": model},
            request_id=request_id,
        )
        db.add(job)
        await db.commit()
        await db.refresh(job)
        logger.info("job_enqueued", extra={"kind": kind, "job_id": job.id})
        return job.id


async def latest_job(kind: str, user_id: int) -> dict | None:
    """Most recent job of one kind, as a plain dict for an API response."""
    async with AsyncSessionLocal() as db:
        job = (
            await db.execute(
                select(BackgroundJob)
                .where(BackgroundJob.user_id == user_id, BackgroundJob.kind == kind)
                .order_by(BackgroundJob.created_at.desc())
                .limit(1)
            )
        ).scalars().first()
    if job is None:
        return None
    return {
        "id": job.id,
        "status": job.status,
        "attempts": job.attempts,
        "last_error": job.last_error or None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


async def enqueue_post_turn(
    *, session_id: int, request_id: str, user_id: int, model: str
) -> int:
    """
    Commit the intention to run this turn's post-turn work.

    Deduplicates against work already queued for the same session and kind: a
    burst of turns in one conversation should leave one pending recap, not eight.
    The job that does run picks up everything since the watermark anyway, so
    collapsing them loses nothing and saves the LLM calls.
    """
    queued = 0
    try:
        async with AsyncSessionLocal() as db:
            existing = set(
                (
                    await db.execute(
                        select(BackgroundJob.kind).where(
                            BackgroundJob.session_id == session_id,
                            BackgroundJob.status.in_(("pending", "running")),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for kind in POST_TURN_KINDS:
                if kind in existing:
                    continue
                db.add(
                    BackgroundJob(
                        user_id=user_id,
                        session_id=session_id,
                        kind=kind,
                        payload={"model": model},
                        request_id=request_id,
                    )
                )
                queued += 1
            await db.commit()
    except Exception as e:  # noqa: BLE001
        # Never let bookkeeping break the turn. If this fails the old behaviour
        # is what we fall back to, which is what run_post_turn_tasks does.
        logger.error(
            "post_turn_enqueue_failed",
            extra={"request_id": request_id, "session_id": session_id, "error": str(e)},
        )
        return 0

    logger.info(
        "post_turn_enqueued",
        extra={"request_id": request_id, "session_id": session_id, "queued": queued},
    )
    return queued


# ── Claim / complete ──────────────────────────────────────────────────────────

async def _claim(db: AsyncSession, limit: int) -> list[BackgroundJob]:
    """Take up to `limit` due jobs and mark them running in one transaction.

    Single-writer deployment, so this needs no row-level locking beyond the
    transaction itself — but the status flip is committed BEFORE any handler
    runs, so a second drain overlapping this one cannot pick the same rows up.
    """
    now = datetime.now(timezone.utc)
    rows = list(
        (
            await db.execute(
                select(BackgroundJob)
                .where(
                    BackgroundJob.status == "pending",
                    BackgroundJob.run_after <= now,
                )
                .order_by(BackgroundJob.created_at.asc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    for job in rows:
        job.status = "running"
        job.started_at = now
        job.attempts += 1
    if rows:
        await db.commit()
    return rows


async def _finish(job_id: int, *, error: str | None) -> None:
    """Record the outcome of one attempt in its own short transaction."""
    async with AsyncSessionLocal() as db:
        job = await db.get(BackgroundJob, job_id)
        if job is None:
            return
        if error is None:
            job.status = "done"
            job.last_error = ""
        elif job.attempts >= MAX_ATTEMPTS:
            job.status = "failed"
            job.last_error = error[:2000]
            logger.error(
                "background_job_gave_up",
                extra={
                    "job_id": job.id,
                    "kind": job.kind,
                    "attempts": job.attempts,
                    "error": error[:500],
                },
            )
        else:
            delay = BACKOFF_MINUTES[min(job.attempts - 1, len(BACKOFF_MINUTES) - 1)]
            job.status = "pending"
            job.last_error = error[:2000]
            job.run_after = datetime.now(timezone.utc) + timedelta(minutes=delay)
            logger.warning(
                "background_job_retry_scheduled",
                extra={
                    "job_id": job.id,
                    "kind": job.kind,
                    "attempt": job.attempts,
                    "retry_in_minutes": delay,
                    "error": error[:500],
                },
            )
        job.updated_at = datetime.now(timezone.utc)
        await db.commit()


async def _run_one(job_id: int, kind: str, session_id, user_id: int, model: str,
                   request_id: str) -> bool:
    handler = _handlers().get(kind)
    if handler is None:
        await _finish(job_id, error=f"no handler registered for kind '{kind}'")
        return False
    try:
        await handler(session_id, request_id, user_id, model)
    except Exception as e:  # noqa: BLE001
        await _finish(job_id, error=f"{type(e).__name__}: {e}")
        return False
    await _finish(job_id, error=None)
    return True


# ── Drain ─────────────────────────────────────────────────────────────────────

async def drain(limit: int = DEFAULT_DRAIN_LIMIT) -> dict:
    """
    Execute due jobs. Returns a summary — the shape the admin endpoint reports.

    Handlers run concurrently (they already did, under `asyncio.gather`) but each
    settles its own row independently, so one failure neither hides nor rolls
    back its siblings. That isolation is the point: the previous
    `return_exceptions=True` collected failures into a list nobody read.
    """
    async with AsyncSessionLocal() as db:
        claimed = await _claim(db, limit)
        # Read the fields out before the session closes — the handlers open their
        # own sessions and must not touch these instances.
        work = [
            (j.id, j.kind, j.session_id, j.user_id,
             (j.payload or {}).get("model", ""), j.request_id)
            for j in claimed
        ]

    if not work:
        return {"claimed": 0, "succeeded": 0, "failed": 0}

    results = await asyncio.gather(
        *(_run_one(*item) for item in work), return_exceptions=True
    )
    # A raised exception here means _run_one itself broke (not the handler, which
    # it catches) — the row stays `running` and the stale-claim sweep recovers it.
    succeeded = sum(1 for r in results if r is True)
    failed = len(results) - succeeded

    logger.info(
        "background_jobs_drained",
        extra={"claimed": len(work), "succeeded": succeeded, "failed": failed},
    )
    return {"claimed": len(work), "succeeded": succeeded, "failed": failed}


async def reclaim_stale(minutes: int = STALE_CLAIM_MINUTES) -> int:
    """
    Return claims orphaned by a crash to the queue.

    A row left `running` has no process behind it — this event loop is the only
    one that claims, so anything still running from before a restart is by
    definition abandoned. The attempt it already consumed is not refunded, so a
    job that reliably kills the process is parked as `failed` rather than
    crash-looping the container.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    async with AsyncSessionLocal() as db:
        rows = list(
            (
                await db.execute(
                    select(BackgroundJob).where(
                        BackgroundJob.status == "running",
                        BackgroundJob.started_at.is_not(None),
                        BackgroundJob.started_at < cutoff,
                    )
                )
            )
            .scalars()
            .all()
        )
        for job in rows:
            if job.attempts >= MAX_ATTEMPTS:
                job.status = "failed"
                job.last_error = "abandoned mid-run (process restart) after final attempt"
            else:
                job.status = "pending"
                job.run_after = datetime.now(timezone.utc)
                job.last_error = "abandoned mid-run (process restart) — requeued"
        if rows:
            await db.commit()
    if rows:
        logger.warning("background_jobs_reclaimed", extra={"count": len(rows)})
    return len(rows)


async def recover_on_startup() -> int:
    """
    Lifespan hook: pick up whatever the last process left behind.

    Reclaims orphaned claims with a zero-minute cutoff — at startup there is no
    such thing as a legitimately in-flight job, because the loop that would have
    been running it no longer exists — then kicks off a drain WITHOUT awaiting it.

    The reclaim is awaited because it is a couple of DB statements. The drain is
    not, because it can make LLM calls and startup must not block behind a
    backlog of them; the app should be serving requests while last night's
    orphaned title is being written. Detaching is safe here in a way it was not
    for the post-turn tasks themselves: the jobs are rows on disk, so if this
    task dies with the process again, the next startup or n8n's sweep still finds
    them. Durability lives in the table, not in the task.

    Returns the number of claims reclaimed.
    """
    reclaimed = await reclaim_stale(minutes=0)

    async def _drain_detached() -> None:
        try:
            result = await drain()
            if result["claimed"]:
                logger.info("background_jobs_startup_recovery", extra=result)
        except Exception as e:  # noqa: BLE001
            logger.error("background_jobs_startup_drain_failed", extra={"error": str(e)})

    asyncio.create_task(_drain_detached())
    return reclaimed


async def queue_stats() -> dict:
    """Counts by status, for the admin endpoint and health checks."""
    from sqlalchemy import func

    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(BackgroundJob.status, func.count())
                .group_by(BackgroundJob.status)
            )
        ).all()
    return {status: count for status, count in rows}
