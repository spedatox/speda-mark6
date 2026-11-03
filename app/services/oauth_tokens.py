"""Per-application token caches; refreshes serialize and credential changes invalidate."""
import asyncio
import hashlib
import json
import time


class CachedOAuthToken:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._credential_hash = None
        self._failed_until = 0.0

    def credential_material(self):
        raise NotImplementedError

    async def get(self):
        async with self._lock:
            current = self._fingerprint()
            if current != self._credential_hash:
                self.clear()
                self._failed_until = 0.0
            elif time.monotonic() < self._failed_until:
                return None
            result = await self._get_unlocked()
            # A provider may rotate the stored refresh token during this call.
            self._credential_hash = self._fingerprint()
            self._failed_until = time.monotonic() + 10 if result is None else 0.0
            return result

    def _fingerprint(self):
        return hashlib.sha256(json.dumps(self.credential_material()).encode()).hexdigest()
