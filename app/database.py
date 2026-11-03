from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import event
from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from sqlalchemy import text

from app.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_pre_ping=True,
)

if engine.dialect.name == "sqlite":
    @event.listens_for(engine.sync_engine, "connect")
    def sqlite_options(connection, _):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=100")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()
        connection.isolation_level = None

    @event.listens_for(engine.sync_engine, "begin")
    def sqlite_begin(connection):
        connection.exec_driver_sql("BEGIN")

AsyncSessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


class UTCNaive(TypeDecorator):
    """Existing timestamp-without-time-zone columns store UTC on both engines.

    SQLite silently accepts aware values; asyncpg correctly rejects that mismatch.
    Normalize bindings without changing the inherited column types or timestamps.
    """
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value


class Base(DeclarativeBase):
    type_annotation_map = {datetime: UTCNaive()}


async def init_db() -> None:
    """Create all tables. Called in the lifespan handler before anything else."""
    # Import all models so their tables are registered on Base.metadata
    import app.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if engine.dialect.name == "sqlite":
            from app.services.memory_store import init_indexes
            await init_indexes(conn)
            from app.services.solus.guards import install_guards
            await conn.run_sync(install_guards)


async def close_db() -> None:
    """Dispose the connection pool. Called on shutdown."""
    await engine.dispose()


@asynccontextmanager
async def write_session(sessions):
    """Acquire SQLite's writer slot before reading mutable coordination state.

    A deferred WAL read cannot later upgrade after another writer commits;
    SQLite returns BUSY_SNAPSHOT immediately regardless of busy_timeout. The
    zero-row native-control UPDATE acquires the writer slot without changing
    any row and works with both the production BEGIN hook and injected engines.
    PostgreSQL retains the existing row locks. Read-only memory sessions never
    enter this path. Acquisition precedes every tool/provider effect and is not
    a retry of a partially executed transaction.
    """
    async with sessions() as db, db.begin():
        if db.bind.dialect.name == 'sqlite':
            await db.execute(text('PRAGMA busy_timeout=5000'))
            await db.execute(text('UPDATE coord_control SET version=version WHERE 0'))
        yield db


async def get_db() -> AsyncSession:  # type: ignore[return]
    """FastAPI dependency that yields a database session."""
    async with AsyncSessionLocal() as session:
        yield session
