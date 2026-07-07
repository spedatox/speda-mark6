import hmac
import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings

logger = logging.getLogger(__name__)

# Paths that bypass authentication entirely.
#   /health                 — liveness probe (no sensitive data)
#   /oauth/google/callback  — Google's redirect can't carry our header
#   /oauth/notion/callback  — Notion's redirect can't carry our header either
# /docs, /redoc, /openapi.json are NOT here — they expose the full API schema.
# They are disabled outside DEBUG (see main.create_app) and, when enabled in
# DEBUG, are allowed through below for local convenience only.
UNPROTECTED_PATHS = frozenset({"/health", "/oauth/google/callback", "/oauth/notion/callback"})

# Prefix-matched exemptions (path parameters can't be enumerated).
#   /telegram/webhook/{agent_id} — Telegram's callback can't carry our X-API-Key;
#   it is instead authenticated by the X-Telegram-Bot-Api-Secret-Token header,
#   validated in the router (constant time), plus an owner-id allowlist in the
#   gateway. See docs/TELEGRAM_ARCHITECTURE.md TG-5.
UNPROTECTED_PREFIXES: tuple[str, ...] = ("/telegram/webhook/",)

_DOCS_PATHS = frozenset({"/docs", "/redoc", "/openapi.json"})


class AuthMiddleware(BaseHTTPMiddleware):
    """Validates the X-API-Key on every request before any router logic runs
    (CLAUDE.md Rule 12). The key lives in the environment as SPEDA_API_KEY. The
    n8n trigger endpoint additionally validates X-N8N-Secret inside its router.
    Compared in constant time. A request with a missing or wrong key is rejected
    with 401 before reaching any router."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if path in UNPROTECTED_PATHS or request.method == "OPTIONS":
            return await call_next(request)

        if path.startswith(UNPROTECTED_PREFIXES):
            return await call_next(request)

        # In DEBUG only, let the interactive docs through for local development.
        if settings.debug and path in _DOCS_PATHS:
            return await call_next(request)

        api_key = request.headers.get("X-API-Key", "")
        if api_key and hmac.compare_digest(api_key, settings.speda_api_key):
            return await call_next(request)

        logger.warning(
            "auth_rejected",
            extra={"path": path, "method": request.method},
        )
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or missing API key"},
        )
