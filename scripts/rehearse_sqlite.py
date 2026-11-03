"""Consistent read-only snapshots and original-row checks for a migration rehearsal.

Never points the application at the source database. Rehearsal artifacts belong
in a private operations directory and contain owner data; retain them for rollback.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3


def readonly(path):
    return sqlite3.connect(Path(path).resolve(strict=True).as_uri() + '?mode=ro', uri=True, timeout=30)


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def integrity(connection):
    result = connection.execute('PRAGMA integrity_check').fetchall()
    if result != [('ok',)]:
        raise ValueError('SQLite integrity check failed')


def snapshot(source, destination):
    source, destination = Path(source).resolve(strict=True), Path(destination).resolve()
    if source == destination or destination.exists():
        raise ValueError('Snapshot requires a new destination separate from the source')
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents concurrent invocations from replacing a backup.
    with destination.open('xb'):
        pass
    destination.chmod(0o600)
    with readonly(source) as original, sqlite3.connect(destination) as backup:
        original.backup(backup, pages=1024, sleep=.05)
        integrity(backup)
    with destination.open('rb') as stream:
        checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'snapshot': str(destination), 'bytes': destination.stat().st_size,
            'sha256': checksum, 'integrity': 'ok'}


def row_digest(connection, table, columns):
    hashes = []
    for row in connection.execute(f'SELECT {",".join(map(quote, columns))} FROM {quote(table)}'):
        values = [('bytes', value.hex()) if isinstance(value, bytes) else (type(value).__name__, value) for value in row]
        hashes.append(hashlib.sha256(json.dumps(values, ensure_ascii=True, separators=(',', ':')).encode()).digest())
    digest = hashlib.sha256()
    for value in sorted(hashes):
        digest.update(value)
    return len(hashes), digest.hexdigest()


def verify(original, candidate):
    with readonly(original) as before, readonly(candidate) as after:
        integrity(before)
        integrity(after)
        before.execute('BEGIN')
        after.execute('BEGIN')
        tables = [r[0] for r in before.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        counts, failures = {}, []
        for table in tables:
            columns = [r[1] for r in before.execute(f'PRAGMA table_info({quote(table)})')]
            try:
                expected = row_digest(before, table, columns)
                actual = row_digest(after, table, columns)
                counts[table] = expected[0]
                if expected != actual:
                    failures.append(table)
            except sqlite3.Error:
                failures.append(table)
        return {'preserved': not failures, 'original_tables': len(tables), 'original_rows': sum(counts.values()),
                'changed_original_tables': failures, 'integrity': 'ok'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    capture = sub.add_parser('snapshot')
    capture.add_argument('source')
    capture.add_argument('destination')
    comparison = sub.add_parser('verify')
    comparison.add_argument('original')
    comparison.add_argument('candidate')
    args = parser.parse_args()
    result = snapshot(args.source, args.destination) if args.command == 'snapshot' else verify(args.original, args.candidate)
    print(json.dumps(result))
    if result.get('preserved') is False:
        raise SystemExit(1)
