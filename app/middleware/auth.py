import logging
import hmac

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings

logger = logging.getLogger(__name__)

# Paths that bypass API key validation
UNPROTECTED_PATHS = frozenset({"/health", "/health/live", "/health/ready", "/docs", "/openapi.json", "/redoc", "/rooms-ui"})
OAUTH_BROWSER_PATHS = frozenset(f'/oauth/{provider}/{step}'
    for provider in ('google','microsoft','notion') for step in ('start','callback'))
UNPROTECTED_PREFIXES = ()


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Validates X-API-Key on every request except health and docs endpoints."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if request.method=='POST' and path.startswith('/telegram/webhook/') and len(path.split('/'))==4:
            # The handler requires Telegram's secret header and a paired owner.
            return await call_next(request)
        if path.startswith('/solus/api/') and request.url.scheme != 'https' and (not request.client or request.client.host not in {'127.0.0.1','::1'}):
            return JSONResponse(status_code=403, content={'detail':'Use HTTPS for Solus.'})
        if request.url.path in UNPROTECTED_PATHS:
            return await call_next(request)
        if request.method == 'GET' and request.url.path in OAUTH_BROWSER_PATHS:
            # These endpoints require the short-lived one-use state and browser
            # binding created by an API-key-authenticated owner initiation.
            try:
                return await call_next(request)
            finally:
                # Uvicorn formats the access path from this scope when the
                # outer response starts. Never log authorization codes/tickets.
                request.scope['query_string'] = b''

        api_key = request.headers.get("X-API-Key")
        if not api_key or not hmac.compare_digest(api_key, settings.speda_api_key):
            service = getattr(request.app.state,'solus_http',None)
            if service:
                if request.method in {'GET','HEAD'} and (path in {'/solus','/solus/'} or path.startswith('/solus/assets/')):
                    return await call_next(request)
                if (path,request.method) in {('/solus/api/v1/login','POST'),('/solus/api/v1/setup','POST'),('/solus/api/v1/auth-status','GET')}:
                    return await call_next(request)
                if path.startswith('/solus/api/v1/') and not (path=='/solus/api/v1/session' and request.method=='POST'):
                    if await service.authenticate(request): return await call_next(request)
            logger.warning(
                "auth_rejected",
                extra={"path": request.url.path, "method": request.method},
            )
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing API key"},
            )

        return await call_next(request)
