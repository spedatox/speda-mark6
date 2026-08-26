"""
One Google access token for the whole process.

Extracted from mcp/google_rest.py, which is where it used to live and still
re-exports it. The move is a layering fix rather than a tidy-up: services may
import from the layers above them and never below, so with the cache sitting in
`mcp/` any SERVICE that needed a Google call — the Octavius Protocol uploading a
database snapshot to Drive — had to import upwards to get one. The alternative
was a second token cache, which is worse than it sounds: Google rotates refresh
tokens, two caches refresh independently, and the loser of that race starts
handing out a token the other half already invalidated.

So: one cache, in a layer everything above `mcp/` can legally reach.
`google_rest.py` keeps `_Token` as an alias, so routers/connections.py clearing
the cache on disconnect goes on working untouched.
"""

import logging
import time

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class GoogleToken:
    """One cached access token shared across every Google caller. Refreshed from
    the stored refresh token + OAuth client when it's within 60s of expiry."""

    _access: str | None = None
    _exp: float = 0.0

    @classmethod
    async def get(cls) -> str | None:
        from app.core.runtime_state import get_google_refresh_token

        now = time.time()
        if cls._access and now < cls._exp - 60:
            return cls._access

        rt = get_google_refresh_token()
        if not (rt and settings.google_client_id and settings.google_client_secret):
            return None
        try:
            async with httpx.AsyncClient(timeout=15.0) as c:
                r = await c.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "client_id": settings.google_client_id,
                        "client_secret": settings.google_client_secret,
                        "refresh_token": rt,
                        "grant_type": "refresh_token",
                    },
                )
            if r.status_code != 200:
                logger.error(
                    "google_token_refresh_failed",
                    extra={"status": r.status_code, "body": r.text[:200]},
                )
                return None
            tok = r.json()
        except Exception as e:  # noqa: BLE001
            logger.error("google_token_refresh_error", extra={"error": str(e)})
            return None

        cls._access = tok.get("access_token")
        cls._exp = now + int(tok.get("expires_in", 3600))
        return cls._access

    @classmethod
    def clear(cls) -> None:
        cls._access = None
        cls._exp = 0.0


access_token = GoogleToken.get
