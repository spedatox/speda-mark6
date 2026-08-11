import logging
import os
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])

MAX_AGE_SECONDS = 86400  # 24 hours


@router.delete("/outputs")
async def cleanup_outputs(request: Request) -> JSONResponse:
    """
    Delete temporary generated files older than 24 hours from /tmp/speda_outputs/.
    Called by n8n on a daily schedule — do not call this manually in the agentic loop.
    Requires X-API-Key header (enforced by APIKeyMiddleware).
    """
    outputs_dir = settings.temp_outputs_dir
    if not os.path.isdir(outputs_dir):
        return JSONResponse({"deleted": 0, "message": "outputs directory does not exist"})

    now = time.time()
    deleted = 0
    errors = 0

    for filename in os.listdir(outputs_dir):
        filepath = os.path.join(outputs_dir, filename)
        try:
            if os.path.isfile(filepath):
                age = now - os.path.getmtime(filepath)
                if age > MAX_AGE_SECONDS:
                    os.remove(filepath)
                    deleted += 1
        except Exception as e:
            logger.error("cleanup_error", extra={"file": filepath, "error": str(e)})
            errors += 1

    logger.info("cleanup_complete", extra={"deleted": deleted, "errors": errors})
    return JSONResponse({"deleted": deleted, "errors": errors})


@router.post("/tasks/drain")
async def drain_background_jobs(request: Request) -> JSONResponse:
    """
    Execute post-turn work that failed or was orphaned (app/services/task_queue.py).

    The queue is drained inline after every turn, so on a healthy system this
    finds nothing. It exists for the two cases inline draining cannot cover: a
    job that failed every attempt during the turn and is now past its backoff,
    and a claim abandoned by a container restart. n8n calls it on a schedule —
    the backend owns whether work happened, n8n owns when to come and ask.

    Requires X-API-Key (enforced by the auth middleware). Returns what it found,
    so an empty result is a positive health signal rather than silence.
    """
    from app.services.task_queue import drain, queue_stats, reclaim_stale

    reclaimed = await reclaim_stale()
    result = await drain()
    result["reclaimed"] = reclaimed
    result["queue"] = await queue_stats()
    logger.info("admin_tasks_drained", extra=result)
    return JSONResponse(result)


# ── Memory: the v3 migration and maintenance surface ──────────────────────────
# All of these are owner/operator actions, not agent tools. They require
# X-API-Key like the rest of /admin and none of them are reachable from a turn.

_USER_ID = 1


def _memory_model() -> str:
    """Cheap tier on whatever provider the owner has routed Orion to (Rule 10 —
    never a hardcoded id). Extraction and composition are background work."""
    from app.profiles.orion import OrionProfile

    profile = OrionProfile()
    return profile.allocate_model("n8n")


@router.post("/memory/shadow")
async def memory_shadow_report(request: Request) -> JSONResponse:
    """
    Compare every derived surface against the file currently stored, and change
    NOTHING (docs/MEMORY_ARCHITECTURE_V3.md §10, phase 3).

    This is the dress rehearsal for the one irreversible step in v3. Read
    `only_in_stored` on each file: it lists knowledge the file holds that the
    record does not, which is exactly what a flip would lose. A report where
    every file's `only_in_stored_count` is 0 is the green light.
    """
    from app.database import AsyncSessionLocal
    from app.services.memory_render import compare_to_stored

    async with AsyncSessionLocal() as db:
        report = await compare_to_stored(db, _USER_ID)
    at_risk = sum(r.get("only_in_stored_count", 0) for r in report)
    thin = [r["path"] for r in report if r.get("warning")]

    if thin:
        verdict = (
            f"NOT safe — {', '.join(thin)} hold prose the record cannot rebuild. "
            f"Re-run the reindex so the seed's model pass captures it."
        )
    elif at_risk:
        verdict = (
            f"{at_risk} fact(s) exist only in the rendered files; seed or record "
            f"them before flipping."
        )
    else:
        verdict = (
            "The record reproduces every rendered file, and the composed files "
            "have backing. Composition quality is still a judgement — read "
            "owner.md and current.md after /admin/memory/compose."
        )
    return JSONResponse({
        "files": report,
        "at_risk_facts": at_risk,
        "thin_compositions": thin,
        "verdict": verdict,
    })


@router.get("/memory/status")
async def memory_status(request: Request) -> JSONResponse:
    """
    Where the memory record stands — what the settings panel polls after a rebuild.

    Cheap: counts and one shadow report, no model call. Returns the reindex job's
    state alongside the verdict, so the panel can show "running…" and then the
    thing the owner actually needs to read, rather than a bare "done".
    """
    from sqlalchemy import func, select

    from app.database import AsyncSessionLocal
    from app.models.observation import Observation
    from app.services.memory_render import compare_to_stored
    from app.services.task_queue import latest_job

    job = await latest_job("memory_reindex", _USER_ID)

    async with AsyncSessionLocal() as db:
        by_origin = {
            origin: count
            for origin, count in (
                await db.execute(
                    select(Observation.origin, func.count())
                    .where(
                        Observation.user_id == _USER_ID,
                        Observation.deleted_at.is_(None),
                    )
                    .group_by(Observation.origin)
                )
            ).all()
        }
        report = await compare_to_stored(db, _USER_ID)

    at_risk = sum(r.get("only_in_stored_count", 0) for r in report)
    thin = [r["path"] for r in report if r.get("warning")]
    total = sum(by_origin.values())

    # What this verdict may and may not claim changed when derivation was turned
    # off. The record is now a SEARCH INDEX beside the documents, not the thing
    # they are built from — so "every file is reproducible from the record" is no
    # longer a meaningful statement, and leaving it in would be exactly the kind
    # of false green light that let a bad migration through in the first place.
    from app.services.memory_render import RENDERED_FILES

    if job and job["status"] in ("pending", "running"):
        p = job.get("progress")
        verdict = (
            f"Rebuilding the search index — batch {p['done']} of {p['total']}, "
            f"{p['stored']} fact(s) so far. Your documents are not affected."
            if p
            else "Rebuilding the search index. Your documents are not affected."
        )
    elif not RENDERED_FILES:
        # The honest statement: this number describes searchability, nothing else.
        verdict = (
            f"{total} fact(s) indexed for semantic search. The documents under "
            f"/memories are the record itself and are not derived from this — "
            f"rebuilding the index is optional and cannot touch them. "
            f"Run GET /admin/memory/verify for document health."
        )
    elif total == 0:
        verdict = "The index is empty — semantic recall will find nothing until it is built."
    elif thin:
        verdict = (
            f"Needs attention: {', '.join(p.split('/')[-1] for p in thin)} hold prose "
            f"the record cannot rebuild."
        )
    elif at_risk:
        verdict = f"{at_risk} fact(s) exist only in the files, not in the index."
    else:
        verdict = f"{total} fact(s) indexed; every derived file is reproducible."

    return JSONResponse({
        "job": job,
        "observations": total,
        "by_origin": by_origin,
        "at_risk_facts": at_risk,
        "thin_compositions": thin,
        "verdict": verdict,
    })


@router.get("/memory/verify")
async def memory_verify(request: Request) -> JSONResponse:
    """
    Check every memory document against its declared grammar
    (docs/MEMORY_ARCHITECTURE_V4.md §3.3). Read-only — it repairs nothing.

    This is the mechanism whose absence let a corrupted generation sit in
    academic.md for three weeks, a paragraph live above the H1 in current.md, and
    ops.md route agents to a directory renamed months ago. Orion runs it nightly;
    it is exposed here so the owner can see the same report on demand.
    """
    from app.database import AsyncSessionLocal
    from app.services.memory_verify import verify_all

    async with AsyncSessionLocal() as db:
        report = await verify_all(db, _USER_ID)
    return JSONResponse(report)


@router.post("/memory/split")
async def memory_split(request: Request) -> JSONResponse:
    """
    Split the registry monoliths into one file per entity
    (memory_spec.COLLECTIONS): projects.md → /memories/projects/<name>.md,
    social.md → /memories/social/<category>/<name>.md.

    **Dry run by default.** Pass `{"apply": true}` to write. The dry run is not a
    formality — its `sections_seen` histogram is the only honest source for
    filling in `CollectionSpec.sections`, which is deliberately left empty until
    the real 38 KB document has been read rather than guessed at.

    The operation only ever CREATES files. The source document is neither edited
    nor deleted, so its preamble is not lost and reverting is deleting what this
    added. Retiring the original is a separate decision for the owner once the
    folder reads correctly.
    """
    from app.database import AsyncSessionLocal
    from app.services.memory_split import split_all

    body = {}
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — an empty body means a dry run
        pass

    apply = bool(body.get("apply"))
    request_id = getattr(request.state, "request_id", "") or "admin-split"
    async with AsyncSessionLocal() as db:
        report = await split_all(
            db, _USER_ID, dry_run=not apply, request_id=request_id
        )
    return JSONResponse(report)


@router.post("/memory/render")
async def memory_render(request: Request) -> JSONResponse:
    """
    Regenerate the six derived surfaces from the record.

    Idempotent: files whose content is unchanged are not rewritten, so this does
    not churn `updated_at` or the revision trail. Normally invoked by the
    post-turn queue; exposed here for a manual rebuild after a bulk correction.
    """
    from app.database import AsyncSessionLocal
    from app.services.memory_render import commit_rendered

    request_id = getattr(request.state, "request_id", "") or "admin-render"
    async with AsyncSessionLocal() as db:
        changed = await commit_rendered(db, _USER_ID, request_id=request_id, author="admin")
    return JSONResponse({"changed": changed})


@router.post("/memory/compose")
async def memory_compose(request: Request) -> JSONResponse:
    """
    Rebuild owner.md and current.md from the record with a model.

    Normally part of Orion's nightly audit. Every claim must cite observation ids
    that exist, or the composition is rejected and the previous version stands —
    so a bad run is a no-op, never a corrupted biography.
    """
    from app.services.memory_compose import compose

    request_id = getattr(request.state, "request_id", "") or "admin-compose"
    report = await compose(_USER_ID, _memory_model(), request_id=request_id)
    return JSONResponse(report)


@router.post("/memory/reindex")
async def memory_reindex(request: Request, seed: bool = True) -> JSONResponse:
    """
    Rebuild the record from the entire conversation history (v3 §6).

    Safe to re-run. Only `origin="reindex"` rows are replaced; owner-authored,
    seeded and live-recorded observations are preserved untouched, which is what
    makes improving the extraction prompt free rather than destructive.

    `seed=true` (the default) additionally parses the pre-v3 markdown files into
    observations on the FIRST run only — it self-guards, so leaving it on is
    harmless. This is a long job: it walks every session and makes one cheap
    model call per batch. Call it from a terminal, not from a UI that will time
    out, and read the returned counts.
    """
    from app.services.memory_reindex import reindex

    request_id = getattr(request.state, "request_id", "") or "admin-reindex"
    report = await reindex(
        _USER_ID, _memory_model(), request_id=request_id, seed=seed
    )
    return JSONResponse(report)
