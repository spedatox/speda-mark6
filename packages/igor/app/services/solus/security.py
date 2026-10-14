# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Password hashing and bounded, persistent login admission. No credential logging."""
import asyncio
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException
from sqlalchemy import text, update
from app.models.solus_ledger import SolusCredential, SolusAuthAttempt, SolusBrowserSession


def password_hash(password):
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise ValueError("Use a password or passphrase between 12 and 256 characters.")
    salt = secrets.token_bytes(16)
    value = hashlib.scrypt(password.encode(), salt=salt, n=2**17, r=8, p=1, maxmem=256*1024*1024, dklen=64)
    return f"scrypt$131072$8$1${salt.hex()}${value.hex()}"


def password_matches(password, encoded):
    if not isinstance(password, str) or len(password) > 256:
        return False
    _, n, r, p, salt, expected = encoded.split('$')
    value = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), maxmem=256*1024*1024, dklen=64)
    return hmac.compare_digest(value.hex(), expected)


class SolusSecurity:
    def __init__(self, session_factory):
        self.session_factory = session_factory
        self.hash_slots = asyncio.Semaphore(2)

    async def configured(self):
        async with self.session_factory() as db:
            return await db.get(SolusCredential, 1) is not None

    async def reserve_attempt(self, request):
        now = datetime.now(timezone.utc)
        peer = request.client.host if request.client else 'unknown'
        # Never trust arbitrary forwarded headers. The deployment supplies real
        # peer addresses only through uvicorn's trusted reverse-proxy handling.
        buckets = [(hashlib.sha256(peer.encode()).hexdigest(), 5), ('installation', 40)]
        async with self.session_factory() as db:
            await db.execute(text('BEGIN IMMEDIATE'))
            for name, limit in buckets:
                attempt = await db.get(SolusAuthAttempt, name)
                if attempt is None:
                    attempt = SolusAuthAttempt(bucket=name, count=0, window_at=now)
                    db.add(attempt)
                elif attempt.window_at.replace(tzinfo=timezone.utc) <= now - timedelta(minutes=15):
                    attempt.window_at, attempt.count = now, 0
                if attempt.count >= limit:
                    await db.rollback()
                    raise HTTPException(429, 'Too many login attempts. Try again in 15 minutes.', headers={'Retry-After':'900'})
                attempt.count += 1
            await db.commit()

    async def verify(self, request, password):
        await self.reserve_attempt(request)
        async with self.session_factory() as db:
            credential = await db.get(SolusCredential, 1)
            if not credential:
                raise HTTPException(401, 'Password login is not configured.')
            async with self.hash_slots:
                matched = await asyncio.to_thread(password_matches, password, credential.password_hash)
            if not matched:
                raise HTTPException(401, 'Invalid password.')
            peer = request.client.host if request.client else 'unknown'
            bucket = await db.get(SolusAuthAttempt, hashlib.sha256(peer.encode()).hexdigest())
            if bucket:
                bucket.count = 0
                await db.commit()
            # Bind the issued session to this credential edition. A password
            # change racing this login must revoke or prevent the new session.
            return credential.version

    async def change(self, request, current_password, new_password, *, setup=False):
        if not setup:
            await self.verify(request, current_password)
        try:
            async with self.hash_slots:
                encoded = await asyncio.to_thread(password_hash, new_password)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        async with self.session_factory() as db:
            await db.execute(text('BEGIN IMMEDIATE'))
            credential = await db.get(SolusCredential, 1)
            if setup and credential:
                raise HTTPException(409, 'Password already configured. Change it in Settings using the current password.')
            if credential and not setup:
                async with self.hash_slots:
                    if not await asyncio.to_thread(password_matches, current_password, credential.password_hash):
                        raise HTTPException(409, 'Credentials changed; log in again.')
                credential.password_hash, credential.version, credential.updated_at = encoded, credential.version + 1, datetime.now(timezone.utc)
            elif setup:
                db.add(SolusCredential(user_id=1, password_hash=encoded))
            else:
                raise HTTPException(409, 'Configure a password first.')
            await db.execute(update(SolusBrowserSession).values(revoked=True))
            await db.commit()
        return {'changed': True, 'sessions_revoked': True}
