# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Owner-facing transport, scoped browser credentials and request contexts."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import secrets
from uuid import uuid4
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from app.core.context import AgentContext
from app.models.solus_ledger import SolusBrowserSession, SolusCredential, SolusSetupToken
from app.services.memory_store import run_financial_command
from app.services.solus import reports
from app.services.solus.commands import SolusError
from app.services.solus.security import SolusSecurity


class SolusHTTP:
    def __init__(self, session_factory):
        self.session_factory = session_factory
        self.security = SolusSecurity(session_factory)

    async def authenticate(self, request):
        token = request.cookies.get("solus_session", "")
        if not token:
            return False
        async with self.session_factory() as db:
            session = await db.get(SolusBrowserSession, sha256(token.encode()).hexdigest())
            if not session or session.revoked or session.expires_at.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc):
                return False
            now = datetime.now(timezone.utc)
            if session.last_seen_at.replace(tzinfo=timezone.utc) <= now - timedelta(minutes=30):
                return False
            credential = await db.get(SolusCredential, session.user_id)
            if session.credential_version and (not credential or credential.version != session.credential_version):
                return False
            if request.method not in {"GET", "HEAD"}:
                origin = request.headers.get("Origin")
                if origin and origin != str(request.base_url).rstrip('/'):
                    return False
                csrf = request.headers.get("X-Solus-CSRF", "")
                if not csrf or not hmac.compare_digest(sha256(csrf.encode()).hexdigest(), session.csrf_hash):
                    return False
            if session.last_seen_at.replace(tzinfo=timezone.utc) < now - timedelta(minutes=1):
                session.last_seen_at = now
                await db.commit()
            request.state.solus_user_id = session.user_id
            return True

    async def session(self, request, *, create=False, delete=False, credential_version=0):
        async with self.session_factory() as db:
            if create:
                from sqlalchemy import text
                await db.execute(text('BEGIN IMMEDIATE'))
                if credential_version:
                    credential = await db.get(SolusCredential, 1)
                    if not credential or credential.version != credential_version:
                        raise HTTPException(409, 'Credentials changed; log in again.')
                token, csrf = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
                old = request.cookies.get("solus_session", "")
                previous = await db.get(SolusBrowserSession, sha256(old.encode()).hexdigest()) if old else None
                if previous:
                    previous.revoked = True
                db.add(SolusBrowserSession(token_hash=sha256(token.encode()).hexdigest(), user_id=1,
                    csrf_hash=sha256(csrf.encode()).hexdigest(), expires_at=datetime.now(timezone.utc) + timedelta(hours=8), credential_version=credential_version))
                await db.commit()
                response = JSONResponse({"authenticated": True, "csrf": csrf})
                secure = request.url.scheme == "https"
                response.set_cookie("solus_session", token, max_age=28800, httponly=True, secure=secure, samesite="strict", path="/solus/api/v1")
                response.set_cookie("solus_csrf", csrf, max_age=28800, secure=secure, samesite="strict", path="/solus")
            elif delete:
                token = request.cookies.get("solus_session", "")
                previous = await db.get(SolusBrowserSession, sha256(token.encode()).hexdigest()) if token else None
                if previous:
                    previous.revoked = True
                    await db.commit()
                response = JSONResponse({"authenticated": False})
                response.delete_cookie("solus_session", path="/solus/api/v1")
                response.delete_cookie("solus_csrf", path="/solus")
            else:
                response = JSONResponse({"authenticated": True, "csrf": request.cookies.get("solus_csrf", "")})
            response.headers["Cache-Control"] = "no-store"
            return response

    async def login(self, request, password):
        version = await self.security.verify(request, password)
        return await self.session(request, create=True, credential_version=version)

    async def receipt(self, request, key):
        from sqlalchemy import select
        from app.models.solus_ledger import SolusCommand
        async with self.session_factory() as db:
            user_id=self.context(request,db).user_id
            saved=await db.scalar(select(SolusCommand).where(SolusCommand.user_id==user_id,SolusCommand.command_key==key))
            if not saved:
                raise HTTPException(404,'No saved receipt yet. Retry the unchanged form using its retained command key.')
            return JSONResponse(saved.receipt,headers={'Cache-Control':'no-store'})

    async def resolve_receipt(self, request, key):
        from app.services.memory_store import resolve_financial_command
        async with self.session_factory() as db:
            try:
                result = await resolve_financial_command(self.context(request, db), key)
                return JSONResponse(result, headers={'Cache-Control':'no-store'})
            except SolusError as exc:
                raise HTTPException(exc.status, str(exc)) from None

    async def setup(self, request, new_password, token):
        if token:
            await self.security.reserve_attempt(request)
            async with self.session_factory() as db:
                from sqlalchemy import text
                await db.execute(text('BEGIN IMMEDIATE'))
                setup = await db.get(SolusSetupToken, sha256(token.encode()).hexdigest())
                if not setup or setup.redeemed or setup.expires_at.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc):
                    raise HTTPException(401, 'Setup link is invalid or expired.')
                setup.redeemed = True
                await db.commit()
        else:
            # Initial setup also works with the installation owner's API key.
            from app.config import settings
            key = request.headers.get('X-API-Key', '')
            if not key or not hmac.compare_digest(key, settings.speda_api_key):
                raise HTTPException(401, 'Use the owner API key or an unused setup link.')
        return await self.security.change(request, '', new_password, setup=True)

    async def preview(self, request, book_id, payload):
        from app.services.solus.imports import parse_csv
        from app.services.solus.commands import apply
        from sqlalchemy import text
        async with self.session_factory() as db:
            try:
                await db.execute(text('BEGIN IMMEDIATE'))
                context = self.context(request, db)
                source_hash, rows = await parse_csv(db, context.user_id, book_id, payload.get('csv'))
                for row in rows:
                    await apply(db, row, context, [])
                await db.rollback()
                return JSONResponse({'source_hash': source_hash, 'rows': [r.model_dump(mode='json') for r in rows]}, headers={'Cache-Control':'no-store'})
            except (SolusError, ValueError) as exc:
                await db.rollback()
                raise HTTPException(getattr(exc, 'status', 422), str(exc)) from None

    async def reconciliation_preview(self, request, book_id, payload):
        from app.services.solus.reconciliation import preview
        async with self.session_factory() as db:
            try:
                user_id = self.context(request, db).user_id
                return JSONResponse(await preview(db, user_id, book_id, payload), headers={'Cache-Control':'no-store'})
            except (ValueError, KeyError) as exc:
                raise HTTPException(getattr(exc, 'status', 422), str(exc)) from None

    def context(self, request, db):
        return AgentContext(agent_id="owner", user_id=getattr(request.state, "solus_user_id", 1), session_id=0,
            request_id=str(uuid4()), triggered_by="user", trigger_payload={}, output_mode="respond", model="",
            system_prompt="", conversation_history=[], db=db, timezone="UTC")

    async def command(self, request, payload):
        async with self.session_factory() as db:
            try:
                result = await run_financial_command(self.context(request, db), payload)
                return JSONResponse(result, headers={"Cache-Control": "no-store"})
            except SolusError as exc:
                raise HTTPException(exc.status, str(exc)) from None
            except ValidationError as exc:
                raise HTTPException(422, exc.errors(include_context=False, include_url=False)) from None
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None

    async def read(self, request, resource, book_id=None):
        async with self.session_factory() as db:
            from sqlalchemy import text
            # SQLite's legacy driver does not begin a snapshot for SELECT alone.
            # Each multi-query report must see one committed ledger edition.
            await db.execute(text('BEGIN'))
            user_id = self.context(request, db).user_id
            query = request.query_params
            try:
                if resource == "books":
                    result = await reports.books(db, user_id)
                elif resource == "accounts":
                    result = await reports.accounts(db, user_id, book_id)
                elif resource == "events":
                    result = await reports.events(db, user_id, book_id, before=query.get("before"), event_id=query.get("event_id"))
                elif resource == "summary":
                    result = await reports.summary(db, user_id, book_id, query.get("period", ""))
                elif resource == "schedules":
                    result = await reports.schedules(db, user_id, book_id, period=query.get("period"), after=query.get("after"))
                elif resource == "plans":
                    result = await reports.plans(db, user_id, book_id)
                elif resource == "statement":
                    result = await reports.statement(db, user_id, book_id, query.get("account_id"), query.get("period", ""))
                elif resource == "forecast":
                    result = await reports.forecast(db, user_id, book_id, query.get("period", ""))
                elif resource == "business":
                    result = await reports.business_report(db, user_id, book_id, query.get("period", ""))
                elif resource == "reconciliations":
                    result = await reports.reconciliations(db, user_id, book_id)
                elif resource == "positions":
                    from app.services.solus.investments import positions
                    result = await positions(db,user_id,book_id,query.get('period',''))
                elif resource == "legacy":
                    from app.services.solus.migration import inventory
                    result = await inventory(db, user_id)
                else:
                    raise SolusError("Unknown report.", 404)
                return JSONResponse(result, headers={"Cache-Control": "no-store"})
            except SolusError as exc:
                raise HTTPException(exc.status, str(exc)) from None
