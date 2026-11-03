import asyncio
import pytest
from sqlalchemy import event,text
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from app.database import write_session


async def test_competing_wal_writers_acquire_before_read_and_do_not_block_readers(tmp_path):
    engine=create_async_engine('sqlite+aiosqlite:///'+str(tmp_path/'coordination.db'))
    @event.listens_for(engine.sync_engine,'connect')
    def options(connection,_):
        connection.isolation_level=None
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('PRAGMA busy_timeout=100')
    @event.listens_for(engine.sync_engine,'begin')
    def begin(connection): connection.exec_driver_sql('BEGIN')
    sessions=async_sessionmaker(engine,expire_on_commit=False)
    try:
        async with engine.begin() as db:
            await db.execute(text('CREATE TABLE coord_control(user_id INTEGER PRIMARY KEY,version INTEGER)'))
            await db.execute(text('INSERT INTO coord_control VALUES(1,0)'))
        entered=asyncio.Event()
        async def increment(first=False):
            async with write_session(sessions) as db:
                version=await db.scalar(text('SELECT version FROM coord_control WHERE user_id=1'))
                if first:
                    entered.set()
                    await asyncio.sleep(.3)  # Longer than the inherited 100ms lock timeout.
                await db.execute(text('UPDATE coord_control SET version=:v WHERE user_id=1'),{'v':version+1})
        first=asyncio.create_task(increment(True))
        await entered.wait()
        second=asyncio.create_task(increment())
        async def read():
            async with sessions() as db:
                return await db.scalar(text('SELECT version FROM coord_control WHERE user_id=1'))
        assert await asyncio.wait_for(read(),.2)==0
        await asyncio.wait_for(asyncio.gather(first,second),5)
        assert await read()==2
    finally:
        await engine.dispose()
