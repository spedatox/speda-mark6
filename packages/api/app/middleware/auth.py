import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.auth.security import constant_time_equals, verify_token
from app.config import settings

logger = logging.getLogger(__name__)

# Paths that bypass authentication entirely.
#   /health                 — liveness probe (no sensitive data)
#   /auth/login             — you must be able to reach it to get a token
#   /oauth/google/callback  — Google's redirect can't carry our header
# NOTE: /docs, /openapi.json, /redoc are intentionally NOT here — they expose the
# full API schema. They are disabled outside DEBUG (see main.create_app) and,
# when enabled in DEBUG, are allowed through below for local convenience only.
UNPROTECTED_PATHS = frozenset({
    "/health", "/auth/login", "/oauth/google/callback",
})

_DOCS_PATHS = frozenset({"/docs", "/redoc", "/openapi.json"})


class AuthMiddleware(BaseHTTPMiddleware):
    """Authenticates every request before it reaches a router.

    Two credential types are accepted, in order:
      1. Authorization: Bearer <jwt>  — owner login session (humans/browser).
      2. X-API-Key: <key>             — service credential (desktop app, scripts).

    The n8n trigger additionally validates X-N8N-Secret inside its router. A
    request with neither valid credential is rejected with 401 before any router
    logic runs (CLAUDE.md Rule 12). The authenticated principal is attached to
    request.state.principal for downstream use.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if path in UNPROTECTED_PATHS or request.method == "OPTIONS":
            return await call_next(request)

        # In DEBUG only, let the interactive docs through for local development.
        if settings.debug and path in _DOCS_PATHS:
            return await call_next(request)

        # 1. Bearer JWT (owner login session).
        authz = request.headers.get("Authorization", "")
        if authz.startswith("Bearer "):
            payload = verify_token(settings.jwt_secret, authz[7:].strip())
            if payload:
                request.state.principal = payload.get("sub", "owner")
                return await call_next(request)

        # 2. Service API key.
        api_key = request.headers.get("X-API-Key")
        if api_key and constant_time_equals(api_key, settings.speda_api_key):
            request.state.principal = "service"
            return await call_next(request)

        logger.warning(
            "auth_rejected",
            extra={"path": path, "method": request.method},
        )
        return JSONResponse(
            status_code=401,
            content={"detail": "Authentication required"},
        )
