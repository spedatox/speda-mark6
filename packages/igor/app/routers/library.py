"""Authenticated, thin Hall of Records routes; source logic lives in services."""
from fastapi import APIRouter, BackgroundTasks, Request

router = APIRouter(prefix="/library", tags=["library"])


@router.get("/sources/{citation_id}")
async def get_citation_source(citation_id: str, request: Request):
    return await request.app.state.library.citation_source(citation_id)


@router.post("/reconcile")
async def reconcile(request: Request, background_tasks: BackgroundTasks, full: bool = False):
    result = await request.app.state.library.reconcile_probe(request.headers.get("X-N8N-Secret", ""), full=full)
    background_tasks.add_task(request.app.state.library.drain_jobs)
    return result
