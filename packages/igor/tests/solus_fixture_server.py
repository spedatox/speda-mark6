# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Isolated browser acceptance fixture. Refuses to use a normal Igor database."""
import os
from pathlib import Path
from tempfile import gettempdir
from contextlib import asynccontextmanager
from fastapi import FastAPI
from sqlalchemy import select

if os.environ.get('SOLUS_TESTING') != '1':
    raise RuntimeError('This fixture requires SOLUS_TESTING=1.')
database_url = os.environ.get('DATABASE_URL', '')
db_path = Path(database_url.removeprefix('sqlite+aiosqlite:///')).resolve()
if not database_url.startswith('sqlite+aiosqlite:///') or not db_path.is_relative_to(Path(gettempdir()).resolve()):
    raise RuntimeError('Browser fixture database must be in the OS temporary directory.')

import app.models
from app.config import settings
from app.database import Base, engine, AsyncSessionLocal
from app.models.user import User
from app.routers.solus import router
from app.services.solus.http import SolusHTTP
from app.services.solus.guards import install_guards
from app.middleware.auth import AuthMiddleware
from app.middleware.security import SecurityHeadersMiddleware


@asynccontextmanager
async def lifespan(app):
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.run_sync(install_guards)
    async with AsyncSessionLocal() as db:
        if not await db.get(User, 1):
            db.add(User(id=1,name='Fixture owner',timezone='Europe/Istanbul'))
            await db.commit()
    app.state.solus_http = SolusHTTP(AsyncSessionLocal)
    yield
    await engine.dispose()


app = FastAPI(lifespan=lifespan)
app.include_router(router)
app.add_middleware(AuthMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
