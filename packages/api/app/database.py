import logging

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.config import settings, _DATA_DIR

logger = logging.getLogger(__name__)

# SQLite needs NullPool (no persistent connections) and the data dir to exist
_is_sqlite = settings.database_url.startswith("sqlite")
if _is_sqlite:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    **{"poolclass": NullPool} if _is_sqlite else {"pool_pre_ping": True},
)

if _is_sqlite:
    # SQLite is the production store (single-user workload), so it must be
    # configured for concurrent access — the default rollback journal + no busy
    # timeout throws "database is locked" the moment a background task (memory
    # extraction, history indexing, news poll, an n8n trigger) writes while the
    # chat loop is writing. Set once per DBAPI connection:
    #   WAL          — readers never block the writer and vice versa
    #   busy_timeout — a second writer waits/retries (5s) instead of erroring
    #   synchronous=NORMAL — safe under WAL, far fewer fsyncs
    #   foreign_keys=ON    — SQLite leaves FK enforcement OFF by default
    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

AsyncSessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


class Base(DeclarativeBase):
    pass


def _apply_additive_migrations(sync_conn) -> None:
    """Additive, idempotent schema reconciliation.

    create_all only creates MISSING TABLES — it never ALTERs an existing one —
    so a column added to a model after its table was first created must be
    backfilled here. Every step is existence-guarded, so this is safe to run on
    every startup and on both SQLite (dev) and Postgres (prod). Only additive
    changes belong here; anything destructive needs a real migration.
    """
    from sqlalchemy import inspect, text

    insp = inspect(sync_conn)
    tables = set(insp.get_table_names())

    # Multi-tenant: which agent owns/voices a watcher. New column on an existing
    # table — create_all won't add it, so ALTER it in when missing.
    if "automations" in tables:
        cols = {c["name"] for c in insp.get_columns("automations")}
        if "agent_id" not in cols:
            sync_conn.execute(
                text("ALTER TABLE automations ADD COLUMN agent_id VARCHAR(64) DEFAULT 'speda'")
            )
            logger.info("schema_migrated", extra={"change": "automations.agent_id"})
        sync_conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_automations_agent_id ON automations (agent_id)")
        )

    # Agent-scoped session listing + conversation-compaction columns.
    if "sessions" in tables:
        scols = {c["name"] for c in insp.get_columns("sessions")}
        if "summary" not in scols:
            sync_conn.execute(text("ALTER TABLE sessions ADD COLUMN summary TEXT"))
            logger.info("schema_migrated", extra={"change": "sessions.summary"})
        if "summary_through_id" not in scols:
            sync_conn.execute(text("ALTER TABLE sessions ADD COLUMN summary_through_id INTEGER"))
            logger.info("schema_migrated", extra={"change": "sessions.summary_through_id"})
        if "recap" not in scols:
            sync_conn.execute(text("ALTER TABLE sessions ADD COLUMN recap TEXT"))
            logger.info("schema_migrated", extra={"change": "sessions.recap"})
        if "recap_through_id" not in scols:
            sync_conn.execute(text("ALTER TABLE sessions ADD COLUMN recap_through_id INTEGER"))
            logger.info("schema_migrated", extra={"change": "sessions.recap_through_id"})
        if "channel" not in scols:
            sync_conn.execute(
                text("ALTER TABLE sessions ADD COLUMN channel VARCHAR(16) DEFAULT 'app'")
            )
            logger.info("schema_migrated", extra={"change": "sessions.channel"})
        sync_conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_sessions_user_agent_started "
                "ON sessions (user_id, agent_id, started_at)"
            )
        )


async def init_db() -> None:
    """Create all tables and seed the default user. Called in lifespan before anything else."""
    # Import all models so their tables are registered on Base.metadata
    import app.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_apply_additive_migrations)

    await _seed_default_user()


async def _seed_default_user() -> None:
    """Ensure user ID 1 exists. Single-user system — idempotent."""
    from sqlalchemy import select
    from app.models.user import User

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.id == 1))
        if result.scalar_one_or_none() is None:
            session.add(User(id=1, name="SPEDA", timezone="UTC"))
            await session.commit()


async def close_db() -> None:
    """Dispose the connection pool. Called on shutdown."""
    await engine.dispose()


async def get_db() -> AsyncSession:  # type: ignore[return]
    """FastAPI dependency that yields a database session."""
    async with AsyncSessionLocal() as session:
        yield session
