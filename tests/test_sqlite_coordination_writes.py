import asyncio
import pytest
from sqlalchemy import event,text
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from app.database import write_session


@pytest.mark.parametrize('operation', ['source', 'apply', 'refresh'])
async def test_iacon_writers_reserve_before_evidence_reads(memory, operation):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.routers.iacon import router
    from app.schemas.iacon import Command
    from app.services.iacon_catalogue import Catalogue
    from app.services.memory_store import MemoryStore

    source_id, _ = await memory.seed('I like cycling.', role='owner', publish=False)
    command = Command(command_id='writer-test', kind='preference', title='Exercise',
        payload={'text': 'I like cycling.'},
        evidence=[{'source_id': source_id, 'start': 0, 'end': 15, 'quote': 'I like cycling.'}])
    original = Catalogue(memory.store)
    record = await original.apply(1, command) if operation == 'refresh' else None

    # Match production's explicit BEGIN; otherwise aiosqlite's legacy transaction
    # handling does not reproduce a deferred WAL reader failing its write upgrade.
    @event.listens_for(memory.engine.sync_engine, 'connect')
    def explicit_transactions(connection, _):
        connection.isolation_level = None
    @event.listens_for(memory.engine.sync_engine, 'begin')
    def begin(connection):
        connection.exec_driver_sql('BEGIN')
    await memory.engine.dispose()
    writers = []
    interleaved = False

    async def compete():
        async with write_session(memory.sessions) as db:
            await db.execute(text("UPDATE users SET name='Competing writer' WHERE id=1"))

    class InterleavedSession(AsyncSession):
        async def execute(self, statement, *args, **kwargs):
            nonlocal interleaved
            result = await super().execute(statement, *args, **kwargs)
            sql = str(statement).lstrip()
            table = 'iacon_work' if operation == 'apply' else 'memory_changes'
            if not interleaved and sql.startswith('SELECT') and f'FROM {table}' in sql:
                interleaved = True
                writers.append(asyncio.create_task(compete()))
                await asyncio.sleep(.15)
            return result

    sessions = async_sessionmaker(memory.engine, class_=InterleavedSession, expire_on_commit=False)
    catalogue = Catalogue(MemoryStore(sessions))
    try:
        if operation == 'source':
            app = FastAPI()
            app.state.iacon = catalogue
            app.include_router(router)
            async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
                body = {'key': 'writer-test', 'text': 'I like cycling.'}
                response = await client.post('/iacon/sources', json=body)
                assert response.status_code == 200
                assert (await client.post('/iacon/sources', json=body)).json() == response.json()
                assert (await client.post('/iacon/sources', json={**body, 'text': 'Changed'})).status_code == 409
        elif operation == 'apply':
            result = await catalogue.apply(1, command)
            assert (await catalogue.apply(1, command))['id'] == result['id']
            assert len((await catalogue.get(1, result['id'], history=True))['revisions']) == 1
        else:
            await catalogue.refresh(1, record['id'])
            assert (await catalogue.get(1, record['id']))['version'] == 1
        assert interleaved
        await asyncio.wait_for(asyncio.gather(*writers), 5)
    finally:
        await asyncio.gather(*writers, return_exceptions=True)


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

@pytest.mark.parametrize('receipt_select', [1, 2], ids=['admission', 'completion'])
async def test_tool_receipts_acquire_writer_before_read_without_replaying_effect(memory, receipt_select):
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.core.registry import CapabilityRegistry
    from app.models.model_policy import ToolExecutionReceipt
    from app.schemas.llm import Block
    from app.skills.base import Skill

    @event.listens_for(memory.engine.sync_engine, 'connect')
    def explicit_transactions(connection, _):
        connection.isolation_level = None
    @event.listens_for(memory.engine.sync_engine, 'begin')
    def begin(connection):
        connection.exec_driver_sql('BEGIN')
    await memory.engine.dispose()
    writers = []
    selects = 0
    async def compete():
        async with write_session(memory.sessions) as db:
            await db.execute(text('UPDATE users SET name=name WHERE id=1'))

    class InterleavedSession(AsyncSession):
        async def execute(self, statement, *args, **kwargs):
            nonlocal selects
            result = await super().execute(statement, *args, **kwargs)
            if str(statement).lstrip().startswith('SELECT') and 'FROM tool_execution_receipts' in str(statement):
                selects += 1
                if selects == receipt_select:
                    writers.append(asyncio.create_task(compete()))
                    await asyncio.sleep(.15)
            return result

    class ReadOnce(Skill):
        name = 'read_once'
        description = 'Read the fixture schedule without changing domain records.'
        input_schema = {'type': 'object', 'properties': {}}
        read_only = True
        calls = 0
        async def execute(self, args, context):
            self.calls += 1
            return 'Schedule read successfully'

    sessions = async_sessionmaker(memory.engine, class_=InterleavedSession, expire_on_commit=False)
    registry = CapabilityRegistry(receipt_sessions=sessions)
    skill = ReadOnce()
    await registry.register_skill(skill)
    context = memory.context()
    call = Block(id='schedule-read', name=skill.name, input={})
    try:
        assert await registry.execute_recorded(call, context) == 'Schedule read successfully'
        await asyncio.wait_for(asyncio.gather(*writers), 5)
        assert await registry.execute_recorded(call, context) == 'Schedule read successfully'
        assert skill.calls == 1
        async with memory.sessions() as db:
            receipt = await db.scalar(select(ToolExecutionReceipt).where(ToolExecutionReceipt.request_id == context.request_id))
            assert receipt.status == 'completed'
    finally:
        await asyncio.gather(*writers, return_exceptions=True)
