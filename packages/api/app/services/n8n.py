import logging

from fastapi import HTTPException, Request

from app.config import settings

logger = logging.getLogger(__name__)


def validate_n8n_secret(request: Request) -> None:
    """
    Validate the X-N8N-Secret header on trigger endpoint requests.
    Both X-API-Key (middleware) and X-N8N-Secret (this function) must pass for /trigger.
    """
    secret = request.headers.get("X-N8N-Secret")
    if not secret or secret != settings.n8n_secret:
        logger.warning(
            "n8n_auth_rejected",
            extra={"path": str(request.url.path)},
        )
        raise HTTPException(status_code=403, detail="Invalid or missing N8N secret")


def format_trigger_context(payload: dict) -> dict:
    """
    Normalise an n8n trigger payload into a standard context dict.
    Adds defaults for fields n8n may omit.
    """
    return {
        "type": payload.get("type", "unknown"),
        "job": payload.get("job"),
        "from_agent": payload.get("from"),
        "event": payload.get("event"),
        "raw": payload,
    }
