"""Host-only SQLite to PostgreSQL cutover using a frozen, verified final copy."""
import argparse
from contextlib import closing
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from scripts.deploy_postgres import POSTGRES_IMAGE, Postgres, PostgresDeployment, rehearse_app
from scripts.deploy_production import Deployment, atomic
from scripts.rehearse_sqlite import readonly, snapshot


HOST_CONFIG = Path('/opt/speda-operations/cicd/config.json')
NETWORK = 'speda-postgres-private'
CONTAINER = 'speda-postgres'
IMPORT_DB = 'speda_rehearsal'
DATABASE = 'speda'
SPARK = 'orion-spark.service'


def mark(root, value):
    print(value, flush=True)
    atomic(root / 'phase', value + '\n')


def prepare(deployment, root):
    root.mkdir(mode=0o700)
    data = root / 'pgdata'
    data.mkdir(mode=0o700)
    deployment.logdir = root
    admin_secret = root / 'admin-secret'
    admin_secret.write_text(secrets.token_urlsafe(48) + '\n')
    password = secrets.token_urlsafe(48)
    (root / 'app-secret').write_text(password + '\n')
    deployment.run(['docker', 'network', 'create', '--internal', NETWORK])
    deployment.run([
        'docker', 'run', '-d', '--name', CONTAINER, '--restart', 'unless-stopped',
        '--network', NETWORK, '-e', 'POSTGRES_PASSWORD_FILE=/run/secrets/postgres-admin',
        '-e', 'POSTGRES_INITDB_ARGS=--auth-host=scram-sha-256 --auth-local=trust',
        '-v', f'{admin_secret}:/run/secrets/postgres-admin:ro', '-v', f'{data}:/var/lib/postgresql/data',
        POSTGRES_IMAGE,
    ])
    store = Postgres(CONTAINER, IMPORT_DB)
    store.wait_ready()
    store.sql(f"CREATE ROLE speda LOGIN PASSWORD '{password}';\nCREATE DATABASE {IMPORT_DB} OWNER speda;",
              database='postgres')
    mark(root, 'Private PostgreSQL target prepared')


def migrate(deployment, root, cases, store, image, password):
    final = root / 'source.sqlite3'
    snapshot_report = snapshot(deployment.config['database'], final)
    atomic(root / 'sqlite-snapshot.json', json.dumps(snapshot_report, indent=2) + '\n')
    mark(root, 'SQLite snapshot captured and integrity checked')
    credentials = root / 'import.env'
    auth = 'speda:' + password if password else 'speda'
    credentials.write_text(f'SPEDA_REHEARSAL_POSTGRES_URL=postgresql+asyncpg://{auth}@127.0.0.1:5432/{store.database}\n')
    try:
        runner = ['docker', 'run', '--rm', '--network', 'container:' + store.container,
                  '--env-file', str(credentials), '-e', 'SPEDA_DATA_DIR=/empty',
                  '-v', f'{root}:/evidence', '-v', f'{cases}:/cases.json:ro']
        deployment.run(runner + [image, 'python', '-m', 'scripts.rehearse_postgres',
                                 '/evidence/source.sqlite3', '/evidence/import.json'], timeout=900)
        mark(root, 'Active PostgreSQL rows matched to the SQLite snapshot')
        store.sql('ANALYZE;')
        checks = Path(__file__).resolve().parent
        for filename, arguments in (
            ('compare_postgres_recall.py', ['/cases.json', '/evidence/recall.json', '--require-parity']),
            ('compare_hall_postgres.py', ['/evidence/hall.json']),
        ):
            script = '/checks/' + filename
            deployment.run(runner + ['-v', f'{checks / filename}:{script}:ro', image,
                                     'python', script, '/evidence/source.sqlite3', *arguments], timeout=420)
        mark(root, 'Recall outputs matched exactly')
    finally:
        credentials.unlink(missing_ok=True)
    return snapshot_report


def verify_backup(deployment, root, store, image):
    dump = root / 'before-open.pgdump'
    report = store.dump(dump)
    columns = store.columns()
    expected = store.fingerprints(columns)
    with store.restore_check(deployment, dump, columns, expected) as recovery:
        rehearse_app(deployment, image, recovery, exercise=True)
    atomic(root / 'postgres-backup.json', json.dumps({**report, 'restore_and_rows_verified': True}, indent=2) + '\n')
    mark(root, 'PostgreSQL backup restored and authenticated writes survived restart')
    return columns, expected


def rehearse(deployment, root, cases, image):
    from uuid import uuid4
    root.mkdir(mode=0o700)
    deployment.logdir = root
    store = Postgres('speda-pg-cutover-check-' + uuid4().hex[:12], IMPORT_DB)
    try:
        deployment.run(['docker', 'run', '-d', '--name', store.container, '--network', 'none',
                        '-e', 'POSTGRES_HOST_AUTH_METHOD=trust', POSTGRES_IMAGE])
        store.wait_ready()
        store.sql(f'CREATE ROLE speda LOGIN;\nCREATE DATABASE {IMPORT_DB} OWNER speda;', database='postgres')
        migrate(deployment, root, cases, store, image, '')
        verify_backup(deployment, root, store, image)
    finally:
        subprocess.run(['docker', 'rm', '-fv', store.container], capture_output=True, timeout=30)


def postgres_config(deployment, target, password):
    config = deployment.config
    overlay = target / 'postgres-host.yml'
    env = target / 'deploy.env'
    prior = Path(config['deploy_env']).read_text()
    if any(line.startswith('SPEDA_POSTGRES_DSN=') for line in prior.splitlines()):
        raise RuntimeError('Previous environment already defines a PostgreSQL connection')
    env.write_text(prior.rstrip('\n') + f'\nSPEDA_POSTGRES_DSN=postgresql+asyncpg://speda:{password}@{CONTAINER}:5432/{DATABASE}\n')
    overlay.write_text('''services:
  app:
    environment:
      DATABASE_URL: "${SPEDA_POSTGRES_DSN:?Set the private PostgreSQL DSN}"
    networks:
      existing: {}
      postgres_private: {}
networks:
  postgres_private:
    external: true
    name: speda-postgres-private
''')
    new = {**config, 'deploy_env': str(env), 'compose_files': config['compose_files'] + [str(overlay)],
           'postgres': {'container': CONTAINER, 'database': DATABASE}}
    candidate = PostgresDeployment(new)
    result = subprocess.run(candidate.compose + ['config', '--format', 'json'],
                            capture_output=True, timeout=30, check=True)
    effective = json.loads(result.stdout)['services']['app']
    expected = f'postgresql+asyncpg://speda:{password}@{CONTAINER}:5432/{DATABASE}'
    if (effective['environment']['DATABASE_URL'] != expected
            or set(effective['networks']) != {'existing', 'postgres_private'}):
        raise RuntimeError('Compose did not preserve the existing network and add PostgreSQL')
    return candidate


def public_status(deployment, expected):
    url = deployment.config['public_url'].rstrip('/') + '/health/ready'
    try:
        with urlopen(url, timeout=15) as response:
            status = response.status
    except HTTPError as error:
        status = error.code
    if status != expected:
        raise RuntimeError(f'Public readiness returned HTTP {status}, expected {expected}')


def citation_checks(deployment, identities):
    state = Path(deployment.config['state_dir'])
    key = json.loads((state / 'configuration.json').read_text())['values']['speda_api_key']
    results = {}
    for citation in identities:
        request = Request('http://127.0.0.1:8000/iacon/hall-of-records/sources/' + citation,
                          headers={'X-API-Key': key})
        try:
            with urlopen(request, timeout=6) as response:
                results[citation] = (response.status, hashlib.sha256(response.read()).hexdigest())
        except HTTPError as error:
            if error.code not in {403, 404, 410}:
                raise
            results[citation] = (error.code, None)
    if not any(status == 200 for status, _ in results.values()):
        raise RuntimeError('No existing Hall of Records citation resolved through the application')
    return results


def reset_database_override(deployment, image):
    script = """
from uuid import uuid4
from urllib.parse import urlsplit
from app.services.config_store import ConfigStore
store = ConfigStore()
state = store.read()
prior = state['values'].get('database_url')
if prior is not None:
    if urlsplit(prior).scheme != 'sqlite+aiosqlite':
        raise RuntimeError('Refusing to reset an unexpected database override')
    store.save(state['revision'], {}, 'postgres-cutover-' + uuid4().hex, reset=['database_url'])
"""
    deployment.run(['docker', 'run', '--rm', '--network', 'none',
                    '-e', 'SPEDA_DATA_DIR=/state', '-v', deployment.config['state_dir'] + ':/state',
                    image, 'python', '-c', script])


def switch(deployment, target, cases, commit):
    current = json.loads((deployment.root / 'current.json').read_text())
    app = deployment.config['app_container']
    n8n = deployment.config['n8n_container']
    if current['commit'] != commit or deployment.run(['docker', 'inspect', app, '--format', '{{.Image}}']) != current['image_id']:
        raise RuntimeError('The expected CI-verified release is not live')
    deployment.check_backend()
    root = target / ('cutover-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()))
    root.mkdir(mode=0o700)
    deployment.logdir = root
    password = (target / 'app-secret').read_text().strip()
    candidate = postgres_config(deployment, target, password)
    candidate.logdir = root
    store = Postgres(CONTAINER, IMPORT_DB)
    store.wait_ready()
    if store.sql("SELECT count(*) FROM pg_tables WHERE schemaname='public'") != '0':
        raise RuntimeError('The final PostgreSQL import target is occupied')
    with closing(readonly(deployment.config['database'])) as source:
        citation_ids = [row[0] for row in source.execute('SELECT id FROM library_citations ORDER BY id')]
    citations = citation_checks(deployment, citation_ids)
    was_spark_running = deployment.run(
        ['systemctl', 'show', SPARK, '-p', 'ActiveState', '--value']) not in {'inactive', 'failed'}
    was_n8n_running = deployment.running(n8n)
    inhibit = deployment.root / 'orion-spark.inhibit'
    if inhibit.exists():
        raise RuntimeError('A prior deployment hold is in effect')
    original_config = HOST_CONFIG.read_text()
    (root / 'host-config-before.json').write_text(original_config)
    shutil.copy2(deployment.config['deploy_env'], root / 'deploy-before.env')
    proxy = root / 'proxy-original.json'
    app_stop_requested = False
    candidate_started = False
    opened = False
    proxy_restored = False
    can_reopen = True
    sqlite = None
    reset_requested = False
    try:
        inhibit.touch(exist_ok=False)
        if was_spark_running:
            deployment.run(['systemctl', 'stop', SPARK])
        deployment.wait_idle()
        deployment.gate()
        public_status(deployment, 503)
        if was_n8n_running:
            deployment.run(['docker', 'stop', '-t', '45', n8n])
        deployment.wait_idle()
        app_stop_requested = True
        deployment.run(['docker', 'stop', '-t', '60', app])
        if deployment.running(app) or not deployment.idle():
            raise RuntimeError('SQLite writers did not quiesce')
        sqlite = readonly(deployment.config['database'])
        frozen_version = sqlite.execute('PRAGMA data_version').fetchone()[0]
        shutil.copytree(deployment.config['state_dir'], root / 'state-before')
        mark(root, 'Production writers paused')
        source = migrate(deployment, root, cases, store, current['image_id'], password)
        store.sql(f'ALTER DATABASE {IMPORT_DB} RENAME TO {DATABASE};', database='postgres')
        columns, expected = verify_backup(deployment, root, candidate.postgres, current['image_id'])
        if sqlite.execute('PRAGMA data_version').fetchone()[0] != frozen_version:
            raise RuntimeError('SQLite changed after writers were paused')
        reset_requested = True
        reset_database_override(deployment, current['image_id'])
        atomic(HOST_CONFIG, json.dumps(candidate.config, indent=2) + '\n')
        candidate_started = True
        candidate.run(candidate.compose + ['up', '-d', '--no-deps', '--no-build', 'app'])
        candidate.check_backend()
        candidate.health(app)
        candidate.authenticated_check()
        if citation_checks(candidate, citation_ids) != citations:
            raise RuntimeError('Live Hall of Records citation or authority response changed')
        atomic(root / 'live-citations.json', json.dumps({
            'checked': len(citations), 'resolved': sum(status == 200 for status, _ in citations.values()),
            'exact_matches': len(citations),
        }, indent=2) + '\n')
        if candidate.run(['docker', 'inspect', app, '--format', '{{.Image}}']) != current['image_id']:
            raise RuntimeError('The running image changed during database cutover')
        opened = True
        candidate.proxy(proxy)
        proxy_restored = True
        public_status(candidate, 200)
        atomic(deployment.root / 'current.json', json.dumps({
            **current, 'database_backend': 'postgresql', 'database_replaced': False,
            'sqlite_cutover_snapshot': source, 'postgres_cutover_evidence': str(root),
        }, indent=2) + '\n')
        mark(root, 'Production serving PostgreSQL')
    except Exception:
        if not opened and candidate_started:
            can_reopen = False
            if candidate.running(app):
                candidate.run(['docker', 'stop', '-t', '60', app])
            can_reopen = candidate.postgres.fingerprints(columns) == expected
        if not opened and can_reopen:
            can_reopen = False
            atomic(HOST_CONFIG, original_config)
            if reset_requested:
                configuration = Path(deployment.config['state_dir']) / 'configuration.json'
                atomic(configuration, (root / 'state-before' / 'configuration.json').read_text())
            if app_stop_requested:
                deployment.run(deployment.compose + ['up', '-d', '--no-deps', '--no-build', 'app'])
                deployment.health(app)
                deployment.authenticated_check()
            can_reopen = True
            mark(root, 'Cutover failed and SQLite production recovered')
        if not opened and not can_reopen:
            mark(root, 'Cutover paused for reconciliation of retained PostgreSQL writes')
        raise
    finally:
        if sqlite is not None:
            sqlite.close()
        if opened or can_reopen:
            if proxy.exists() and not proxy_restored:
                deployment.proxy(proxy)
            if was_n8n_running and not deployment.running(n8n):
                deployment.run(['docker', 'start', n8n])
            inhibit.unlink(missing_ok=True)
            if was_spark_running:
                deployment.run(['systemctl', 'start', SPARK])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['prepare', 'rehearse', 'switch'])
    parser.add_argument('directory', type=Path)
    parser.add_argument('--cases', type=Path)
    parser.add_argument('--image')
    parser.add_argument('--commit')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('This command runs only as root on the production host')
    if (args.mode != 'prepare' and not args.cases) or (args.mode == 'rehearse' and not args.image) or (
            args.mode == 'switch' and not args.commit):
        parser.error('Provide the cases file and exact image or release identity')
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    os.umask(0o077)
    config = json.loads(HOST_CONFIG.read_text())
    if config.get('postgres'):
        parser.error('This host already uses PostgreSQL')
    deployment = Deployment(config)
    if args.mode == 'prepare':
        prepare(deployment, args.directory)
    elif args.mode == 'rehearse':
        rehearse(deployment, args.directory, args.cases, args.image)
    else:
        with (deployment.root / 'deployment.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            switch(deployment, args.directory, args.cases.resolve(strict=True), args.commit)


if __name__ == '__main__':
    main()
