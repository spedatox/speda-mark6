"""Add immutable offline chat transcripts to an existing local SQLite database.

No models, memory extraction, task dispatch, or replacement of existing rows.
The source snapshot stays authoritative; mappings preserve its original IDs.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3


def fingerprint(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def stamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.isoformat(sep=' ', timespec='microseconds')


def insert(db, table, values):
    names = list(values)
    return db.execute(f'INSERT INTO {table} ({",".join(names)}) VALUES ({",".join("?" for _ in names)})',
                      [values[name] for name in names]).lastrowid


def import_chats(snapshot, destination, backup, report, *, source_owner=1, owner=1):
    snapshot, destination = Path(snapshot).resolve(strict=True), Path(destination).resolve(strict=True)
    backup, report = Path(backup).resolve(), Path(report).resolve()
    if len({snapshot, destination, backup, report}) != 4 or backup.exists() or report.exists():
        raise ValueError('Source, existing destination, new backup and new report must be separate paths')
    for suffix in ('-wal', '-journal'):
        sidecar = Path(str(snapshot) + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise ValueError('Use a consistent offline snapshot without an active WAL/journal')
    digest = fingerprint(snapshot)
    source = sqlite3.connect(snapshot.as_uri() + '?mode=ro&immutable=1', uri=True)
    target = sqlite3.connect(destination.as_uri() + '?mode=rw', uri=True, timeout=30)
    source.row_factory = target.row_factory = sqlite3.Row
    try:
        if source.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('Source integrity check failed')
        if target.execute('SELECT id FROM users WHERE id=?', (owner,)).fetchone() is None:
            raise ValueError('Destination owner must already exist')
        source_sessions = list(source.execute('SELECT * FROM sessions WHERE user_id=? ORDER BY id', (source_owner,)))
        if not source_sessions:
            raise ValueError('No sessions for this source owner')
        backup.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation prevents overwriting an earlier rollback copy.
        with backup.open('xb'):
            pass
        with sqlite3.connect(backup) as saved:
            target.backup(saved)
            if saved.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('Rollback backup integrity check failed')
        target.execute('PRAGMA foreign_keys=ON')
        target.execute('BEGIN IMMEDIATE')
        target.execute('''CREATE TABLE IF NOT EXISTS chat_snapshot_imports (
            id INTEGER PRIMARY KEY, snapshot_sha256 TEXT NOT NULL, source_owner INTEGER NOT NULL,
            owner INTEGER NOT NULL REFERENCES users(id), source_path TEXT NOT NULL,
            imported_at TEXT NOT NULL, counts TEXT NOT NULL,
            UNIQUE(snapshot_sha256,source_owner,owner))''')
        target.execute('''CREATE TABLE IF NOT EXISTS chat_snapshot_rows (
            import_id INTEGER NOT NULL REFERENCES chat_snapshot_imports(id), kind TEXT NOT NULL,
            source_id INTEGER NOT NULL, target_id INTEGER, source_sha256 TEXT NOT NULL,
            original_metadata TEXT NOT NULL,
            PRIMARY KEY(import_id,kind,source_id), UNIQUE(kind,target_id))''')
        prior = target.execute('SELECT * FROM chat_snapshot_imports WHERE owner=?', (owner,)).fetchall()
        matching = next((r for r in prior if r['snapshot_sha256'] == digest and r['source_owner'] == source_owner), None)
        result = {'snapshot_sha256': digest, 'source': str(snapshot), 'destination': str(destination),
                  'backup': str(backup), 'source_owner': source_owner, 'owner': owner}
        if matching:
            result.update(status='already_imported', counts=json.loads(matching['counts']))
        else:
            if prior:
                raise ValueError('A different snapshot was already imported; reconcile overlapping history before adding another')
            batch = insert(target, 'chat_snapshot_imports', dict(snapshot_sha256=digest, source_owner=source_owner,
                owner=owner, source_path=str(snapshot), imported_at=datetime.now(timezone.utc).isoformat(), counts='{}'))
            counts = {'sessions': 0, 'messages': 0, 'tool_calls': 0, 'archived_agent_messages': 0}
            mapping = {}

            def remember(kind, row, target_id, metadata):
                raw = json.dumps(dict(row), ensure_ascii=False, sort_keys=True, separators=(',', ':'))
                target.execute('INSERT INTO chat_snapshot_rows VALUES (?,?,?,?,?,?)',
                    (batch, kind, row['id'], target_id, hashlib.sha256(raw.encode()).hexdigest(),
                     json.dumps(metadata, ensure_ascii=False)))

            fields = ('agent_id', 'triggered_by', 'model_used', 'title', 'started_at', 'ended_at',
                      'token_count_input', 'token_count_output')
            for row in source_sessions:
                values = {name: row[name] for name in fields}
                for name in ('started_at', 'ended_at'):
                    if values[name] is not None:
                        values[name] = stamp(values[name])
                values['user_id'] = owner
                target_id = insert(target, 'sessions', values)
                mapping[row['id']] = target_id
                remember('session', row, target_id, dict(row))
                counts['sessions'] += 1
            # Global source-ID order preserves each session's original turn order.
            for row in source.execute('''SELECT m.* FROM messages m JOIN sessions s ON s.id=m.session_id
                                         WHERE s.user_id=? ORDER BY m.id''', (source_owner,)):
                json.loads(row['content'])  # Refuse corrupt JSON; keep valid content bytes unchanged.
                target_id = insert(target, 'messages', dict(session_id=mapping[row['session_id']], role=row['role'],
                    content=row['content'], created_at=stamp(row['created_at'])))
                remember('message', row, target_id, {'session_id': row['session_id'], 'created_at': row['created_at']})
                counts['messages'] += 1
            tables = {r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'tool_calls' in tables:
                for row in source.execute('''SELECT t.* FROM tool_calls t JOIN sessions s ON s.id=t.session_id
                                             WHERE s.user_id=? ORDER BY t.id''', (source_owner,)):
                    values = dict(row)
                    values.pop('id')
                    values.update(session_id=mapping[row['session_id']], called_at=stamp(row['called_at']))
                    target_id = insert(target, 'tool_calls', values)
                    remember('tool_call', row, target_id, {'session_id': row['session_id']})
                    counts['tool_calls'] += 1
            if 'agent_messages' in tables:
                for row in source.execute('SELECT * FROM agent_messages ORDER BY id'):
                    if row['session_id'] in mapping or row['origin_session_id'] in mapping:
                        # Historical peer records are evidence, never executable native jobs.
                        remember('agent_message', row, None, dict(row))
                        counts['archived_agent_messages'] += 1
            target.execute('UPDATE chat_snapshot_imports SET counts=? WHERE id=?', (json.dumps(counts), batch))
            result.update(status='imported', counts=counts, import_id=batch)
        if fingerprint(snapshot) != digest:
            raise ValueError('Source changed during import; transaction will be rolled back')
        if target.execute('PRAGMA foreign_key_check').fetchone() is not None:
            raise ValueError('Destination foreign-key validation failed; transaction will be rolled back')
        if target.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('Destination integrity check failed; transaction will be rolled back')
        target.commit()
        result['notes'] = ['Existing local sessions/messages retained; source IDs are provenance, not live foreign keys.',
            'Original content blocks and inline images retained; external attachment files are referenced, not bundled.',
            'No memory recapture, provider requests, worker restart or historical tool replay.']
        report.parent.mkdir(parents=True, exist_ok=True)
        with report.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, indent=2)
        return result
    except BaseException:
        target.rollback()
        raise
    finally:
        source.close()
        target.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ('snapshot', 'destination', 'backup', 'report'):
        parser.add_argument('--' + option, required=True)
    parser.add_argument('--source-owner', type=int, default=1)
    parser.add_argument('--owner', type=int, default=1)
    args = parser.parse_args()
    print(json.dumps(import_chats(**vars(args)), indent=2))
