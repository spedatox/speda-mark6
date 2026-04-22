from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


class Base(DeclarativeBase):
    pass


async def init_db() -> None:
    """Create all tables and seed the default user. Called in lifespan before anything else."""
    # Import all models so their tables are registered on Base.metadata
    import app.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

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
