"""Authenticated, thin Hall of Records routes; source logic lives in services."""
from fastapi import APIRouter, BackgroundTasks, Request
from app.schemas.library import LibrarySettingsUpdate, LibraryProgressRequest

router = APIRouter(prefix="/library", tags=["library"])


@router.get("/sources/{citation_id}")
async def get_citation_source(citation_id: str, request: Request):
    return await request.app.state.library.citation_source(citation_id)


@router.post("/reconcile")
async def reconcile(request: Request, background_tasks: BackgroundTasks, full: bool = False):
    result = await request.app.state.library.reconcile_probe(request.headers.get("X-N8N-Secret", ""), full=full)
    if hasattr(request.app.state, "library_controls"):
        await request.app.state.library_controls.retry_due(request.headers.get("X-N8N-Secret", ""))
    background_tasks.add_task(request.app.state.library.drain_jobs)
    return result


@router.get("/settings")
async def get_settings(request: Request):
    return await request.app.state.library_controls.snapshot(request.headers.get("X-N8N-Secret", ""))


@router.put("/settings")
async def save_settings(values: LibrarySettingsUpdate, request: Request, background_tasks: BackgroundTasks):
    result = await request.app.state.library_controls.save(request.headers.get("X-N8N-Secret", ""), values)
    background_tasks.add_task(request.app.state.library.drain_jobs)
    return result


@router.post("/progress")
async def progress(values: LibraryProgressRequest, request: Request):
    return await request.app.state.library_controls.progress(request.headers.get("X-N8N-Secret", ""), values.document_ids)
