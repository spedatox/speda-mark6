import logging

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

    # Agent-scoped session listing. create_all won't add an index to a table
    # that already exists, so ensure it idempotently (no-op on fresh DBs).
    if "sessions" in tables:
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
