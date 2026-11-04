"""Compatibility endpoints used by the deployed Hisar Hall of Records."""
from fastapi import APIRouter, BackgroundTasks, Depends, Request

from app.routers.iacon import context, scheduler
from app.schemas.library import LibraryProgressRequest, LibrarySettingsUpdate

router = APIRouter(prefix="/library", tags=["library"])
control = [Depends(scheduler)]


@router.post("/reconcile", dependencies=control)
async def reconcile(request: Request, tasks: BackgroundTasks, full: bool = False):
    service = request.app.state.library_controls
    ctx = context()
    result = await service.reconcile(ctx, full=full)
    if result["status"] in {"accepted", "time_limit"}:
        tasks.add_task(request.app.state.orion.drain, ctx, library_only=True)
    return result


@router.get("/settings", dependencies=control)
async def settings(request: Request):
    return request.app.state.library_controls.settings()


@router.put("/settings", dependencies=control)
async def save(values: LibrarySettingsUpdate, request: Request):
    return request.app.state.library_controls.save(values)


@router.post("/progress", dependencies=control)
async def progress(values: LibraryProgressRequest, request: Request):
    return await request.app.state.library_controls.progress(values.document_ids)


@router.get("/sources/{citation_id}")
async def source(citation_id: str, request: Request):
    return await request.app.state.library.citation_source(citation_id)
