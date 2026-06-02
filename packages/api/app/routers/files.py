"""File download endpoint — serves deliverable files from temp_outputs_dir."""

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.core.files import safe_output_path, kind_for

logger = logging.getLogger(__name__)
router = APIRouter(tags=["files"])


@router.get("/files/{name}")
async def download_file(name: str):
    """Serve a produced file for download. Path-traversal safe."""
    path = safe_output_path(name)
    if path is None:
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        path,
        filename=path.name,
        media_type="application/octet-stream",  # force download
    )


@router.get("/files/{name}/meta")
async def file_meta(name: str):
    path = safe_output_path(name)
    if path is None:
        raise HTTPException(status_code=404, detail="File not found")
    return {
        "name": path.name,
        "kind": kind_for(path.name),
        "size": path.stat().st_size,
        "url": f"/files/{path.name}",
    }
