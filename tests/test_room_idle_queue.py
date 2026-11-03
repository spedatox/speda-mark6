import asyncio
from types import SimpleNamespace
from sqlalchemy import select,text,event
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from app.database import Base
from app.models.user import User
from app.models.coordination import Room
from app.schemas.coordination import CreateRoom
from tests.test_coordination import setup


async def test_idle_queue_advance_does_not_compete_with_a_live_sqlite_reader(tmp_path):
    engine=create_async_engine('sqlite+aiosqlite:///'+str(tmp_path/'rollback-queue.db'))
    @event.listens_for(engine.sync_engine,'connect')
    def rollback_options(connection,_):
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA busy_timeout=100')
    sessions=async_sessionmaker(engine,expire_on_commit=False)
    async with engine.begin() as connection: await connection.run_sync(Base.metadata.create_all)
    async with sessions() as db,db.begin(): db.add(User(id=1,name='Owner',timezone='Europe/Istanbul'))
    bits=await setup(SimpleNamespace(sessions=sessions),[])
    # The fixture deliberately uses SQLite's rollback journal. A real shared
    # read lock exposes an unnecessary finalizer write even though production
    # normally uses WAL; no timeout inflation masks the contention.
    try:
        room=await bits.coordinator.create(1,CreateRoom())
        async with sessions() as reader:
            await reader.execute(text('BEGIN'))
            assert await reader.scalar(select(Room.id).where(Room.id==room['id']))
            await asyncio.wait_for(bits.coordinator.advance_room(1,room['id']),.5)
            await reader.rollback()
        assert not bits.client.calls
    finally:
        await bits.coordinator.shutdown()
        await engine.dispose()
