"""
Owner login — username/password -> short-lived HS256 JWT.

Zero business logic beyond auth: verify the owner credential, mint a token.
SQL-injection-safe by construction (no DB query here at all — the credential
lives in config, not the database). User-enumeration-safe: the same failure
response and the same scrypt work happen whether the username or the password
was wrong, and brute force is throttled per IP.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.auth.security import constant_time_equals, create_token, verify_password
from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


def _client_key(request: Request) -> str:
    """Rate-limit key. Behind Caddy the real client is the first X-Forwarded-For
    hop; fall back to the socket peer when there is no proxy."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("/login")
async def login(request: Request, body: LoginRequest):
    limiter = request.app.state.login_rate_limiter
    key = _client_key(request)

    allowed, retry_after = limiter.check(key)
    if not allowed:
        logger.warning("login_locked_out", extra={"client": key, "retry_after": retry_after})
        return JSONResponse(
            status_code=429,
            content={"detail": "Too many attempts. Try again later."},
            headers={"Retry-After": str(retry_after)},
        )

    # Fail closed if login isn't configured — never fall open to "no auth".
    if not (settings.owner_username and settings.owner_password_hash and settings.jwt_secret):
        logger.error("login_not_configured")
        # Generic 401 to the client (don't disclose config state); detail in logs.
        limiter.record_failure(key)
        return JSONResponse(status_code=401, content={"detail": "Invalid username or password"})

    # Always run the password hash check (even on username mismatch) so the
    # response timing doesn't reveal whether the username exists.
    username_ok = constant_time_equals(body.username, settings.owner_username)
    password_ok = verify_password(body.password, settings.owner_password_hash)

    if not (username_ok and password_ok):
        limiter.record_failure(key)
        logger.warning("login_failed", extra={"client": key})
        return JSONResponse(status_code=401, content={"detail": "Invalid username or password"})

    limiter.reset(key)
    token, exp = create_token(settings.jwt_secret, settings.owner_username, settings.jwt_ttl_seconds)
    logger.info("login_ok", extra={"client": key})
    return {"token": token, "token_type": "bearer", "expires_at": exp}


@router.get("/me")
async def me(request: Request):
    """Whoami for the authenticated caller. The middleware has already validated
    the token / key and attached the principal; an invalid caller never reaches
    here (it's rejected at the middleware with 401)."""
    principal = getattr(request.state, "principal", None)
    return {"authenticated": principal is not None, "principal": principal}
