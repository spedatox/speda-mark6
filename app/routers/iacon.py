"""Owner-authenticated Iacon control surface; n8n remains the sole scheduler."""
import asyncio
import hmac
from uuid import uuid4
from fastapi import APIRouter, HTTPException, Request, Query
from sqlalchemy import select
from app.core.context import AgentContext
from app.models.iacon import IaconWork, IaconFollowup
from app.schemas.iacon import Command, PolicyUpdate, SourceIntake, WorkResolution, FollowupResolution
from app.services.iacon_catalogue import Conflict

router = APIRouter(prefix="/iacon", tags=["iacon"])


def context():
    from app.config import settings
    return AgentContext(user_id=1, session_id=0, request_id=uuid4().hex, triggered_by="n8n",
        trigger_payload={}, output_mode="silent", model="", system_prompt="", conversation_history=[],
        db=None, timezone=settings.owner_timezone, agent_id="orion", task_role="background")


def scheduler(request):
    from app.config import settings
    if not settings.n8n_secret or not hmac.compare_digest(request.headers.get("X-N8N-Secret", ""), settings.n8n_secret):
        raise HTTPException(401, "Invalid scheduler secret")


@router.post("/sources")
async def source(request: Request, body: SourceIntake):
    from app.services.memory_store import capture_source
    try:
        async with request.app.state.iacon.sessions() as db, db.begin():
            row = await capture_source(db, user_id=1, source_key="owner-intake:" + body.key, content=body.text)
            return {"source_id": row.id, "content_hash": row.content_hash}
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/work/{job_id}/resolve")
async def resolve_work(request: Request, job_id: str, body: WorkResolution):
    try:
        return await request.app.state.orion.resolve(1, job_id, body)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/followups/{identity}/resolve")
async def resolve_followup(request: Request, identity: str, body: FollowupResolution):
    try:
        return await request.app.state.iacon_telegram.resolve(1, identity, body)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/records")
async def records(request: Request, kind: str | None = None, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
    return await request.app.state.iacon.list(1, kind=kind, offset=offset, limit=limit)


@router.get("/records/{record_id}")
async def record(request: Request, record_id: str, history: bool = False):
    try:
        return await request.app.state.iacon.get(1, record_id, history=history)
    except KeyError as exc:
        raise HTTPException(404, "Record not found") from exc


@router.post("/records")
async def write(request: Request, body: Command):
    try:
        return await request.app.state.iacon.apply(1, body)
    except ValueError as exc:
        raise HTTPException(409 if isinstance(exc, Conflict) else 422, str(exc)) from exc


@router.get("/maintenance")
async def maintenance_status(request: Request):
    result = await request.app.state.iacon_budget.status(1)
    async with request.app.state.iacon.sessions() as db:
        jobs = (await db.execute(select(IaconWork).where(IaconWork.user_id == 1,
            IaconWork.status != "done").order_by(IaconWork.updated_at.desc()).limit(100))).scalars()
        result["work"] = [{"id": j.id, "kind": j.kind, "status": j.status, "error": j.error,
                           "attempts": j.attempts} for j in jobs]
        items = (await db.execute(select(IaconFollowup).where(IaconFollowup.user_id == 1,
            IaconFollowup.status.notin_(["superseded", "answered"])).order_by(IaconFollowup.due_at).limit(100))).scalars()
        result["followups"] = [{"id": f.id, "state_id": f.state_id, "version": f.state_version,
                                "status": f.status, "due_at": f.due_at} for f in items]
    return result


@router.put("/maintenance")
async def policy(request: Request, body: PolicyUpdate):
    try:
        return await request.app.state.iacon_budget.save(1, body.expected_version, body.policy)
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/drain")
async def drain(request: Request):
    scheduler(request)
    ctx = context()
    work = await request.app.state.orion.drain(ctx)
    followup = await request.app.state.iacon_telegram.send_due(ctx)
    return {"maintenance": work, "followup": followup}


@router.post("/telegram/update")
async def telegram_update(request: Request):
    # n8n forwards Telegram updates with BOTH API and scheduler credentials.
    scheduler(request)
    if len(await request.body()) > 32768:
        raise HTTPException(413, "Update too large")
    try:
        return await request.app.state.iacon_telegram.receive(context(), await request.json())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/hall-of-records/sync")
async def sync(request: Request, full: bool = False):
    scheduler(request)
    _, policy = await request.app.state.iacon_budget.policy(1)
    if policy.paused:
        return {"status": "paused"}
    try:
        async with asyncio.timeout(policy.run_seconds):
            return await request.app.state.library.indexer.reconcile(full=full)
    except TimeoutError:
        return {"status": "time_limit", "detail": "Committed event cursor retained; resume next run"}


@router.get("/hall-of-records/sources/{citation_id}")
async def citation(request: Request, citation_id: str):
    async with asyncio.timeout(3):
        return await request.app.state.library.citation_source(citation_id)
