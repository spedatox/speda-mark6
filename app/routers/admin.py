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
