"""SessionManager.truncate against the rows that point AT messages.

Editing or regenerating a turn deletes stored messages. Semantic recall holds a
real foreign key to those messages and SQLite runs with foreign_keys=ON, so a
truncate that ignores the embeddings raises "FOREIGN KEY constraint failed" —
which reaches the desktop client as a 500 and reads there as an unreachable
backend. These tests pin the cleanup so that regression cannot come back quietly.
"""

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401  — registers every table on Base.metadata
from app.core.session_manager import SessionManager
from app.database import Base
from app.models.message import Message
from app.models.message_embedding import MessageEmbedding
from app.models.session import Session
from app.models.user import User


@pytest.fixture
async def db_factory(tmp_path):
    """A throwaway SQLite DB configured like production — FK enforcement ON."""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'test.db'}", poolclass=NullPool
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _seed(factory, *, embed_indexes: tuple[int, ...]) -> tuple[int, list[int]]:
    """Three messages in one session; embed the ones named by index."""
    async with factory() as db:
        db.add(User(id=1, name="owner", timezone="UTC"))
        await db.flush()
        session = Session(
            user_id=1, agent_id="speda", triggered_by="user", model_used="m"
        )
        db.add(session)
        await db.flush()

        messages = [
            Message(session_id=session.id, role="user", content="first"),
            Message(session_id=session.id, role="user", content="second"),
            Message(session_id=session.id, role="assistant", content="answer"),
        ]
        db.add_all(messages)
        await db.flush()

        for i in embed_indexes:
            db.add(MessageEmbedding(
                message_id=messages[i].id, session_id=session.id, user_id=1,
                agent_id="speda", role=messages[i].role,
                text=messages[i].content, embedding=b"\x00" * 8,
            ))
        await db.commit()
        return session.id, [m.id for m in messages]


async def test_truncate_drops_embeddings_of_deleted_messages(db_factory):
    # The post-turn indexer has embedded the turn being edited away — the exact
    # state in which editing an older message used to 500.
    session_id, _ = await _seed(db_factory, embed_indexes=(1, 2))

    async with db_factory() as db:
        deleted = await SessionManager().truncate(db, session_id, keep=1)

    assert deleted == 2
    async with db_factory() as db:
        kept = (await db.execute(select(Message.content))).scalars().all()
        embeddings = (await db.execute(select(MessageEmbedding.id))).scalars().all()
    assert kept == ["first"]
    assert embeddings == []  # nothing left pointing at a message that is gone


async def test_truncate_keeps_embeddings_of_surviving_messages(db_factory):
    # Only the vectors of the deleted prefix's tail go; recall over what remains
    # must survive an edit untouched.
    session_id, ids = await _seed(db_factory, embed_indexes=(0, 2))

    async with db_factory() as db:
        await SessionManager().truncate(db, session_id, keep=1)

    async with db_factory() as db:
        remaining = (
            await db.execute(select(MessageEmbedding.message_id))
        ).scalars().all()
    assert remaining == [ids[0]]


async def test_truncate_without_embeddings_is_unchanged(db_factory):
    session_id, _ = await _seed(db_factory, embed_indexes=())

    async with db_factory() as db:
        assert await SessionManager().truncate(db, session_id, keep=2) == 1
        # Nothing left to delete → no work, no error.
        assert await SessionManager().truncate(db, session_id, keep=2) == 0
