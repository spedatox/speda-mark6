import hashlib
import json
import sqlite3

import pytest

from scripts.import_chat_snapshot import import_chats


def database(path):
    db = sqlite3.connect(path)
    db.executescript('''
        CREATE TABLE users (id INTEGER PRIMARY KEY);
        INSERT INTO users VALUES(1);
        CREATE TABLE sessions (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
            agent_id TEXT, triggered_by TEXT, model_used TEXT, title TEXT, started_at TEXT, ended_at TEXT,
            token_count_input INTEGER, token_count_output INTEGER, channel TEXT, project_id INTEGER);
        CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id INTEGER REFERENCES sessions(id),
            role TEXT, content JSON, created_at TEXT);
        CREATE TABLE tool_calls (id INTEGER PRIMARY KEY, session_id INTEGER REFERENCES sessions(id),
            request_id TEXT, tool_name TEXT, tool_input JSON, tool_result JSON, duration_ms INTEGER,
            error TEXT, called_at TEXT);
        CREATE TABLE agent_messages (id INTEGER PRIMARY KEY, session_id INTEGER,
            origin_session_id INTEGER, from_agent TEXT, to_agent TEXT, result TEXT, status TEXT);
    ''')
    return db


def seed_session(db, identity, title, *, owner=1, agent='speda', started='2026-10-10 12:00:00'):
    db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
        (identity, owner, agent, 'user', 'historical-model', title, started, None, 100, 20, 'desktop', 9))


@pytest.fixture
def snapshots(tmp_path):
    source, target = tmp_path / 'source.db', tmp_path / 'target.db'
    image = [{'type': 'text', 'text': 'Original owner text'},
             {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': 'aW1hZ2U='}}]
    answer = [{'type': 'text', 'text': 'Original answer'},
              {'type': '_speda_meta', 'tools': [{'name': 'historical_tool', 'result': 'recorded'}]}]
    with database(source) as db:
        seed_session(db, 1, 'Old main chat')
        seed_session(db, 2, 'Research', agent='nightcrawler')
        db.execute('INSERT INTO users VALUES(2)')
        seed_session(db, 3, 'Other owner', owner=2)
        for identity, sid, role, content in [(1, 1, 'user', image), (2, 1, 'assistant', answer),
                (3, 2, 'user', [{'type': 'text', 'text': 'Research question'}]),
                (4, 3, 'user', [{'type': 'text', 'text': 'Other owner private'}])]:
            db.execute('INSERT INTO messages VALUES(?,?,?,?,?)',
                (identity, sid, role, json.dumps(content), '2026-10-10T12:00:00+00:00'))
        db.execute("INSERT INTO tool_calls VALUES(1,1,'old-request','historical_tool','{}','{}',5,NULL,'2026-10-10 12:00:00')")
        db.execute("INSERT INTO agent_messages VALUES(1,2,1,'speda','nightcrawler','Recorded result','ok')")
    with database(target) as db:
        seed_session(db, 1, 'New local conversation', started='2026-10-29 12:00:00')
        db.execute("INSERT INTO messages VALUES(1,1,'user','[]','2026-10-29 12:00:00')")
    return source, target


def run(paths, tmp_path, suffix='first'):
    source, target = paths
    return import_chats(source, target, tmp_path / f'backup-{suffix}.db', tmp_path / f'report-{suffix}.json')


def test_import_retains_local_chat_and_exact_content_identity_order_and_rollback_copy(snapshots, tmp_path):
    source, target = snapshots
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    result = run(snapshots, tmp_path)
    assert result['counts'] == {'sessions': 2, 'messages': 3, 'tool_calls': 1, 'archived_agent_messages': 1}
    with sqlite3.connect(source) as old, sqlite3.connect(target) as db:
        assert db.execute('SELECT title FROM sessions WHERE id=1').fetchone()[0] == 'New local conversation'
        assert db.execute('SELECT content FROM messages WHERE id=1').fetchone()[0] == '[]'
        assert db.execute('SELECT count(*) FROM sessions').fetchone()[0] == 3
        assert db.execute('SELECT agent_id FROM sessions WHERE id=3').fetchone()[0] == 'nightcrawler'
        assert [r[0] for r in db.execute('SELECT role FROM messages WHERE session_id=2 ORDER BY id')] == ['user', 'assistant']
        originals = [r[0] for r in old.execute('SELECT content FROM messages WHERE session_id=1 ORDER BY id')]
        assert [r[0] for r in db.execute('SELECT content FROM messages WHERE session_id=2 ORDER BY id')] == originals
        assert db.execute('SELECT session_id FROM tool_calls').fetchone()[0] == 2
        assert db.execute('PRAGMA foreign_key_check').fetchone() is None
        metadata = json.loads(db.execute("SELECT original_metadata FROM chat_snapshot_rows WHERE kind='session' AND source_id=1").fetchone()[0])
        assert metadata['channel'] == 'desktop' and metadata['project_id'] == 9
        assert db.execute('SELECT started_at FROM sessions WHERE id=2').fetchone()[0] == '2026-10-10 12:00:00.000000'
    with sqlite3.connect(tmp_path / 'backup-first.db') as saved:
        assert saved.execute('SELECT count(*) FROM sessions').fetchone()[0] == 1
        assert saved.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


def test_repeat_import_never_duplicates_or_overwrites_local_edits(snapshots, tmp_path):
    run(snapshots, tmp_path)
    with sqlite3.connect(snapshots[1]) as db:
        db.execute("UPDATE sessions SET title='Owner renamed it' WHERE id=2")
    second = run(snapshots, tmp_path, 'second')
    assert second['status'] == 'already_imported'
    with sqlite3.connect(snapshots[1]) as db:
        assert db.execute('SELECT count(*) FROM messages').fetchone()[0] == 4
        assert db.execute('SELECT title FROM sessions WHERE id=2').fetchone()[0] == 'Owner renamed it'


def test_invalid_source_message_rolls_back_every_imported_row(snapshots, tmp_path):
    with sqlite3.connect(snapshots[0]) as db:
        db.execute("UPDATE messages SET content='invalid JSON' WHERE id=3")
    with pytest.raises(ValueError):
        run(snapshots, tmp_path)
    with sqlite3.connect(snapshots[1]) as db:
        assert db.execute('SELECT count(*) FROM sessions').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM messages').fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM sqlite_master WHERE name='chat_snapshot_imports'").fetchone()[0] == 0


def test_refuses_active_source_and_refuses_replacing_backups(snapshots, tmp_path):
    wal = tmp_path / 'source.db-wal'
    wal.write_bytes(b'active')
    with pytest.raises(ValueError, match='offline'):
        run(snapshots, tmp_path)
    wal.unlink()
    run(snapshots, tmp_path)
    with pytest.raises(ValueError, match='separate paths'):
        run(snapshots, tmp_path)


def test_different_snapshot_requires_reconciliation(snapshots, tmp_path):
    run(snapshots, tmp_path)
    with sqlite3.connect(snapshots[0]) as db:
        db.execute("UPDATE sessions SET title='Later snapshot' WHERE id=1")
    with pytest.raises(ValueError, match='reconcile'):
        run(snapshots, tmp_path, 'newer')
    with sqlite3.connect(snapshots[1]) as db:
        assert db.execute('SELECT count(*) FROM sessions').fetchone()[0] == 3
