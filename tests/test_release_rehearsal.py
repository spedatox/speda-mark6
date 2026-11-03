import sqlite3
import pytest
from scripts.rehearse_sqlite import snapshot, verify
from scripts.migrate_native_rest import migrate


def test_snapshot_includes_committed_wal_and_refuses_overwrite(tmp_path):
    original, retained = tmp_path/'live.db', tmp_path/'retained.db'
    with sqlite3.connect(original) as live:
        live.execute('PRAGMA journal_mode=WAL')
        live.execute('CREATE TABLE originals(id INTEGER PRIMARY KEY, value TEXT)')
        live.execute("INSERT INTO originals(value) VALUES('source evidence')")
        live.commit()
        assert snapshot(original, retained)['integrity']=='ok'
        assert verify(original, retained)['preserved']
        with pytest.raises(ValueError): snapshot(original, retained)
        with pytest.raises(ValueError): snapshot(original, original)


def test_preservation_allows_additions_but_detects_changed_or_deleted_sources(tmp_path):
    original, candidate = tmp_path/'original.db', tmp_path/'candidate.db'
    with sqlite3.connect(original) as db:
        db.execute('CREATE TABLE originals(id INTEGER PRIMARY KEY, value TEXT, payload BLOB)')
        db.execute('INSERT INTO originals VALUES(1,?,?)', ('unchanged', b'\x01'))
    snapshot(original, candidate)
    with sqlite3.connect(candidate) as db:
        db.execute('ALTER TABLE originals ADD COLUMN new_metadata TEXT')
        db.execute('CREATE TABLE native_view(id INTEGER PRIMARY KEY)')
    assert verify(original, candidate)['preserved']
    with sqlite3.connect(candidate) as db:
        db.execute("UPDATE originals SET value='rewritten'")
    assert verify(original, candidate)['changed_original_tables']==['originals']
    with sqlite3.connect(candidate) as db: db.execute('DROP TABLE originals')
    assert not verify(original, candidate)['preserved']


async def test_unreviewed_schema_change_refuses_apply_before_creating_tables(memory):
    async with memory.engine.begin() as db:
        await db.exec_driver_sql('ALTER TABLE users DROP COLUMN timezone')
    plan=await migrate(memory.engine)
    assert 'users.timezone' in plan['unsupported_columns']
    with pytest.raises(ValueError, match='Unreviewed schema differences'):
        await migrate(memory.engine,apply=True)
