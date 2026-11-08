"""PostgreSQL backup, restore and compatibility gates for the host release driver."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import subprocess
import time
from urllib.parse import urlunsplit
from uuid import uuid4

from scripts.deploy_production import DataPreservationFailure, Deployment, container_database, container_database_env


POSTGRES_IMAGE = 'postgres@sha256:e8a07d2c7579b038938cdba6c539e580e5001caf9ea203d397b5cc83e76bd938'


def identifier(name):
    return '"' + name.replace('"', '""') + '"'


class Postgres:
    def __init__(self, container, database='speda'):
        self.container = container
        self.database = database

    def sql(self, statement, *, database=None):
        result = subprocess.run([
            'docker', 'exec', '-i', self.container, 'psql', '-XqAt', '-v', 'ON_ERROR_STOP=1',
            '-U', 'postgres', '-d', database or self.database,
        ], input=statement, text=True, capture_output=True, timeout=180)
        if result.returncode:
            raise RuntimeError('PostgreSQL administrative statement failed')
        return result.stdout.strip()

    def wait_ready(self):
        deadline = time.monotonic() + 75
        while time.monotonic() < deadline:
            result = subprocess.run(['docker', 'exec', self.container, 'pg_isready',
                                     '-h', '127.0.0.1', '-U', 'postgres'],
                                    capture_output=True, timeout=8)
            if result.returncode == 0:
                return
            time.sleep(1)
        raise RuntimeError('PostgreSQL readiness deadline exceeded')

    def dump(self, path):
        with path.open('xb') as stream:
            subprocess.run(['docker', 'exec', self.container, 'pg_dump', '-Fc',
                            '-U', 'postgres', '-d', self.database],
                           stdout=stream, stderr=subprocess.PIPE, check=True, timeout=240)
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        return {'engine': 'postgresql', 'snapshot': str(path), 'bytes': path.stat().st_size, 'sha256': digest}

    def columns(self):
        return json.loads(self.sql("""
            SELECT json_object_agg(table_name, columns ORDER BY table_name)
            FROM (
                SELECT c.table_name, json_agg(c.column_name ORDER BY c.ordinal_position) AS columns
                FROM information_schema.columns c JOIN information_schema.tables t
                  USING (table_catalog, table_schema, table_name)
                WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE'
                GROUP BY c.table_name
            ) AS tables
        """))

    def fingerprints(self, columns):
        if not columns:
            raise ValueError('PostgreSQL snapshot has no base tables')
        statements = ['BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY']
        for table, names in columns.items():
            selected = ', '.join(map(identifier, names))
            statements.append(f"""
                SELECT count(*), encode(sha256(convert_to(
                    coalesce(string_agg(digest, '' ORDER BY digest COLLATE "C"), ''), 'UTF8')), 'hex')
                FROM (
                    SELECT encode(sha256(convert_to(row_to_json(source.*)::text, 'UTF8')), 'hex') AS digest
                    FROM (SELECT {selected} FROM public.{identifier(table)}) AS source
                ) AS rows
            """)
        statements.append('COMMIT')
        return self.sql(';\n'.join(statements) + ';').splitlines()

    @contextmanager
    def restore_check(self, deployment, dump, columns, expected):
        recovery = Postgres('speda-pg-restore-' + uuid4().hex[:12], self.database)
        try:
            deployment.run([
                'docker', 'run', '-d', '--name', recovery.container, '--network', 'none',
                '-e', 'POSTGRES_HOST_AUTH_METHOD=trust', POSTGRES_IMAGE,
            ])
            recovery.wait_ready()
            recovery.sql(f'CREATE ROLE speda LOGIN;\nCREATE DATABASE {identifier(self.database)} OWNER speda;',
                         database='postgres')
            with dump.open('rb') as stream:
                result = subprocess.run([
                    'docker', 'exec', '-i', recovery.container, 'pg_restore', '--exit-on-error',
                    '--single-transaction', '--no-owner', '--role=speda', '-U', 'postgres', '-d', self.database,
                ], stdin=stream, capture_output=True, timeout=240)
            (deployment.logdir / 'pg-restore.log').write_bytes(result.stdout + result.stderr)
            if result.returncode or recovery.fingerprints(columns) != expected:
                raise RuntimeError('PostgreSQL backup did not restore the original rows')
            recovery.sql('ANALYZE;')
            yield recovery
        finally:
            subprocess.run(['docker', 'rm', '-fv', recovery.container], capture_output=True, timeout=30)


def rehearse_app(deployment, image, store, *, exercise=False):
    name = 'speda-pg-app-check-' + uuid4().hex[:12]
    try:
        deployment.run([
            'docker', 'run', '-d', '--name', name, '--network', 'container:' + store.container,
            '-e', 'SPEDA_DATA_DIR=/empty', '-e', 'DATABASE_URL=postgresql+asyncpg://speda@127.0.0.1:5432/' + store.database,
            '-e', 'SPEDA_API_KEY=isolated-release-check', '-e', 'EXTERNAL_INTEGRATIONS_ENABLED=false',
            '-e', 'MEMORY_CAPTURE_ENABLED=false', image,
        ])
        deployment.health(name)
        command = ['docker', 'exec', name, 'python', '-m', 'scripts.release_smoke']
        if exercise:
            command.append('--exercise-candidate')
        deployment.run(command)
    finally:
        subprocess.run(['docker', 'rm', '-fv', name], capture_output=True, timeout=30)


class PostgresDeployment(Deployment):
    def __init__(self, config):
        super().__init__(config)
        self.postgres = Postgres(**config['postgres'])

    def check_backend(self):
        active = container_database(self.config['app_container'])
        if (active.scheme, active.hostname, active.path) != (
                'postgresql+asyncpg', self.postgres.container, '/' + self.postgres.database):
            raise RuntimeError('The running database does not match the PostgreSQL deployment configuration')

    def idle(self):
        return self.postgres.sql("""
            SELECT NOT EXISTS (
                SELECT 1 FROM coord_runs
                  WHERE status IN ('queued', 'running', 'waiting_tasks', 'waiting_room', 'cancel_requested')
                UNION ALL SELECT 1 FROM coord_project_tasks WHERE status IN ('staging', 'running', 'reconciling')
                UNION ALL SELECT 1 FROM iacon_work WHERE status = 'running'
                UNION ALL SELECT 1 FROM global_spending_calls WHERE status = 'started'
            )
        """) == 't'

    def migrate_postgres(self, image, store, backup, dsn):
        credentials = self.logdir / 'migration.env'
        try:
            credentials.write_text('SPEDA_MIGRATION_DATABASE_URL=' + dsn + '\n')
            return json.loads(self.run([
                'docker', 'run', '--rm', '--network', 'container:' + store.container,
                '--env-file', str(credentials), '-e', 'SPEDA_DATA_DIR=/empty',
                '-v', str(backup.parent) + ':/backup:ro', image,
                'python', '-m', 'scripts.migrate_native_rest', '--apply', '--backup', '/backup/' + backup.name,
            ]))
        finally:
            credentials.unlink(missing_ok=True)

    def prepare_database(self, image, old):
        backup = self.logdir / 'before.pgdump'
        report = self.postgres.dump(backup)
        columns = self.postgres.columns()
        expected = self.postgres.fingerprints(columns)
        with self.postgres.restore_check(self, backup, columns, expected) as recovery:
            self.migrate_postgres(image, recovery, backup, f'postgresql+asyncpg://speda@127.0.0.1:5432/{recovery.database}')
            rehearse_app(self, image, recovery)
            rehearse_app(self, old, recovery)
            if recovery.fingerprints(columns) != expected:
                raise RuntimeError('Schema rehearsal altered existing PostgreSQL rows')
        configured = container_database_env(self.config['app_container'])
        local = urlunsplit(configured._replace(netloc=configured.netloc.rsplit('@', 1)[0] + '@127.0.0.1:5432'))
        migration = self.migrate_postgres(image, self.postgres, backup, local)
        try:
            preserved = self.postgres.fingerprints(columns) == expected
        except Exception as error:
            raise DataPreservationFailure('PostgreSQL preservation could not be verified after applying DDL') from error
        if not preserved:
            raise DataPreservationFailure('Existing PostgreSQL rows changed; writers remain stopped for reconciliation')
        return {**report, 'restore_and_rows_verified': True}, migration
