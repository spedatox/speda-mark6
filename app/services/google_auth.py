# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""App-owned Google token cache, shared by that application's REST clients.

Refreshes serialize; credential changes invalidate previously cached access.
"""

import logging
import time

import httpx
from app.services.oauth_tokens import CachedOAuthToken

from app.config import settings

logger = logging.getLogger(__name__)


class GoogleToken(CachedOAuthToken):
    """One cached access token shared across every Google caller. Refreshed from
    the stored refresh token + OAuth client when it's within 60s of expiry."""

    _access: str | None = None
    _exp: float = 0.0

    async def _get_unlocked(self) -> str | None:
        from app.core.runtime_state import get_google_refresh_token

        now = time.time()
        if self._access and now < self._exp - 60:
            return self._access

        rt = get_google_refresh_token()
        if not (rt and settings.google_client_id and settings.google_client_secret):
            return None
        try:
            async with httpx.AsyncClient(timeout=15.0, trust_env=False) as c:
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
                    extra={"status": r.status_code},
                )
                return None
            tok = r.json()
        except Exception as e:  # noqa: BLE001
            logger.error("google_token_refresh_error", extra={"error_type": type(e).__name__})
            return None

        self._access = tok.get("access_token")
        self._exp = now + int(tok.get("expires_in", 3600))
        return self._access

    def clear(self) -> None:
        self._access = None
        self._exp = 0.0

    def credential_material(self):
        from app.core.runtime_state import get_google_refresh_token
        return (get_google_refresh_token(), settings.google_client_id, settings.google_client_secret)
