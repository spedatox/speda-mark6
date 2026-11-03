"""Host-owned release driver: tested image, frozen backup, additive schema, code rollback.

Install with its SQLite helper on the production host. Private host configuration
selects the existing compose/data/state paths; CI supplies only an image and its
commit manifest. No dataset or credentials are imported from a runner.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import subprocess
import tarfile
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

from scripts.rehearse_sqlite import snapshot, verify


class DataPreservationFailure(RuntimeError):
    """Keep the write gate closed for explicit reconciliation of unexpected DDL effects."""


def verify_loaded_image(expected, actual, commit):
    """Docker stores can rewrite config IDs on load; verify content and runtime config."""
    keys = {'Env', 'Cmd', 'Entrypoint', 'User', 'WorkingDir', 'Labels', 'ExposedPorts',
            'Volumes', 'StopSignal', 'Healthcheck', 'ArgsEscaped'} | set(expected['config'])
    if (expected['rootfs']['diff_ids'] != actual['RootFS']['Layers']
            or expected['os'] != actual['Os'] or expected['architecture'] != actual['Architecture']
            or any((expected['config'].get(k) or None) != (actual['Config'].get(k) or None) for k in keys)
            or actual['Config'].get('Labels', {}).get('org.opencontainers.image.revision') != commit):
        raise ValueError('Loaded image content, runtime configuration or commit differs from CI archive')
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', actual['Id']):
        raise ValueError('Loaded image lacks a valid immutable local identity')
    return actual['Id']


def manifest(payload, upload_root=Path('/tmp')):
    payload = Path(payload)
    if not payload.is_absolute() or payload.is_symlink() or not re.fullmatch(r'speda-deploy-[a-f0-9]{40}-[0-9]+-[0-9]+', payload.name):
        raise ValueError('Use an absolute CI upload directory with commit/run/attempt identity')
    if payload.parent != upload_root:
        raise ValueError('Uploads must be direct children of /tmp')
    for name in ('release.json', 'image.tar.gz'):
        if (payload / name).is_symlink() or not (payload / name).is_file():
            raise ValueError('Missing regular release files')
    value = json.loads((payload / 'release.json').read_text())
    if not re.fullmatch(r'[a-f0-9]{40}', value.get('commit', '')) or not re.fullmatch(r'sha256:[a-f0-9]{64}', value.get('image_id', '')):
        raise ValueError('Invalid immutable release identity')
    if payload.name.split('-')[2] != value['commit']:
        raise ValueError('Upload name and commit disagree')
    with (payload / 'image.tar.gz').open('rb') as stream:
        checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
    if value.get('archive_sha256') != checksum:
        raise ValueError('Uploaded image checksum mismatch')
    return value


def image_env(text, image):
    lines = text.splitlines()
    if sum(line.startswith('SPEDA_IMAGE=') for line in lines) != 1:
        raise ValueError('Private deployment env must declare exactly one image')
    return '\n'.join('SPEDA_IMAGE=' + image if line.startswith('SPEDA_IMAGE=') else line for line in lines) + '\n'


def atomic(path, text):
    path = Path(path)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.deploy-')
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(text); stream.flush(); os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


class Deployment:
    def __init__(self, config):
        self.config = config
        self.root = Path(config['operations_dir'])
        self.logdir = None
        self.sequence = 0
        self.compose = ['docker', 'compose', '--env-file', config['deploy_env'], '-p', config['compose_project']]
        for path in config['compose_files']:
            self.compose += ['-f', path]

    def run(self, argv):
        result = subprocess.run(argv, text=True, capture_output=True, timeout=180)
        self.sequence += 1
        if self.logdir:
            (self.logdir / f'command-{self.sequence}.log').write_text(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError(f'Deployment command {self.sequence} failed; inspect the private host evidence')
        return result.stdout.strip()

    def running(self, container):
        return self.run(['docker', 'inspect', container, '--format', '{{.State.Running}}']) == 'true'

    def load_image(self, upload, release):
        self.run(['docker', 'image', 'load', '-i', str(upload / 'image.tar.gz')])
        with tarfile.open(upload / 'image.tar.gz', 'r:gz') as archive:
            entries = json.load(archive.extractfile('manifest.json'))
            if len(entries) != 1 or 'speda-renovated:ci' not in entries[0].get('RepoTags', []):
                raise ValueError('Expected one tagged production image in the CI archive')
            expected = json.load(archive.extractfile(entries[0]['Config']))
        actual = json.loads(self.run(['docker', 'image', 'inspect', 'speda-renovated:ci', '--format', '{{json .}}']))
        image = verify_loaded_image(expected, actual, release['commit'])
        return {**release, 'ci_image_id': release['image_id'], 'image_id': image,
                'archive_layers_and_runtime_config_verified': True}

    def idle(self):
        # All writers are gated before the final snapshot. Never cancel work to deploy.
        tables = {'coord_runs': ('status', ['queued', 'running', 'waiting_tasks', 'waiting_room', 'cancel_requested']),
                  'coord_project_tasks': ('status', ['staging', 'running', 'reconciling']),
                  'iacon_work': ('status', ['running']), 'global_spending_calls': ('status', ['started'])}
        with sqlite3.connect(Path(self.config['database']).as_uri() + '?mode=ro', uri=True) as db:
            existing = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            total = 0
            for table, (column, states) in tables.items():
                if table in existing:
                    total += db.execute(f'SELECT count(*) FROM "{table}" WHERE "{column}" IN ({",".join("?" for _ in states)})', states).fetchone()[0]
            return total == 0

    def wait_idle(self):
        deadline = time.monotonic() + 150
        while not self.idle():
            if time.monotonic() > deadline:
                raise RuntimeError('Active work did not drain; deployment deferred without cancelling it')
            time.sleep(3)

    def health(self, container):
        deadline = time.monotonic() + 75
        while time.monotonic() < deadline:
            result = subprocess.run(['docker', 'exec', container, 'python', '-c',
                "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health/ready',timeout=3)"], capture_output=True)
            if result.returncode == 0:
                return
            time.sleep(1)
        raise RuntimeError('Application readiness deadline exceeded')

    def proxy(self, path):
        self.run(['docker', 'cp', str(path), self.config['caddy_container'] + ':/tmp/renovated-deploy.json'])
        self.run(['docker', 'exec', self.config['caddy_container'], 'caddy', 'reload', '--config', '/tmp/renovated-deploy.json'])

    def gate(self):
        original = self.run(['docker', 'exec', self.config['caddy_container'], 'wget', '-qO-', 'http://127.0.0.1:2019/config/'])
        retained = self.logdir / 'proxy-original.json'; retained.write_text(original)
        config = json.loads(original); matched = []
        host = urlsplit(self.config['public_url']).hostname
        def visit(value):
            if isinstance(value, dict):
                if any(host in match.get('host', []) for match in value.get('match', [])):
                    value['handle'] = [{'handler': 'static_response', 'status_code': 503,
                        'body': 'SPEDA is updating. Please retry shortly.', 'headers': {'Retry-After': ['30']}}]
                    matched.append(True)
                else:
                    for child in value.values(): visit(child)
            elif isinstance(value, list):
                for child in value: visit(child)
        visit(config)
        if len(matched) != 1:
            raise ValueError('Production proxy route was not uniquely identified')
        maintenance = self.logdir / 'proxy-maintenance.json'; maintenance.write_text(json.dumps(config))
        self.proxy(maintenance)
        return retained

    def migration(self, image, data, backup):
        output = self.run(['docker', 'run', '--rm', '--network', 'none', '-e', 'SPEDA_DATA_DIR=/empty',
            '-v', str(data) + ':/data', '-v', str(backup.parent) + ':/backup:ro', image,
            'python', '-m', 'scripts.migrate_native_rest', '--database-url', 'sqlite+aiosqlite:////data/speda.db',
            '--apply', '--backup', '/backup/' + backup.name])
        result = json.loads(output)
        if result['unsupported_columns']:
            raise RuntimeError('Unreviewed schema differences')
        return result

    def rehearse(self, image, data):
        name = 'renovated-cd-check-' + uuid4().hex[:12]
        try:
            self.run(['docker', 'run', '-d', '--name', name, '--network', 'none',
                '-e', 'SPEDA_DATA_DIR=/empty', '-e', 'DATABASE_URL=sqlite+aiosqlite:////data/speda.db',
                '-e', 'SPEDA_API_KEY=isolated-release-check', '-e', 'EXTERNAL_INTEGRATIONS_ENABLED=false',
                '-e', 'MEMORY_CAPTURE_ENABLED=false', '-v', str(data) + ':/data', image])
            self.health(name)
            self.run(['docker', 'exec', name, 'python', '-m', 'scripts.release_smoke'])
        finally:
            subprocess.run(['docker', 'rm', '-f', name], capture_output=True)

    def authenticated_check(self):
        settings = json.loads((Path(self.config['state_dir']) / 'configuration.json').read_text())['values']
        key = settings['speda_api_key']
        for path in ('/sessions?limit=1', '/models', '/config'):
            request = Request('http://127.0.0.1:8000' + path, headers={'X-API-Key': key})
            with urlopen(request, timeout=10) as response:
                if response.status != 200: raise RuntimeError('Authenticated application check failed')
        try:
            urlopen('http://127.0.0.1:8000/config', timeout=10)
        except HTTPError as error:
            if error.code == 401: return
        raise RuntimeError('Owner authentication boundary check failed')

    def deploy(self, upload):
        import fcntl
        value = manifest(upload)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (self.root / 'deployment.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return self.locked_deploy(Path(upload), value)

    def locked_deploy(self, upload, release):
        app, n8n = self.config['app_container'], self.config['n8n_container']
        old = self.run(['docker', 'inspect', app, '--format', '{{.Image}}'])
        self.logdir = self.root / (release['commit'][:12] + '-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + uuid4().hex[:8])
        self.logdir.mkdir(mode=0o700)
        release = self.load_image(upload, release)
        # A deploy must never re-enable the watchdog the owner stopped.
        if self.run(['systemctl', 'show', 'orion-spark.service', '-p', 'ActiveState', '--value']) not in {'inactive', 'failed'}:
            raise RuntimeError('Orion Spark must remain stopped; inspect its unexpected activation')
        self.wait_idle()
        n8n_running = self.running(n8n)
        env_path = Path(self.config['deploy_env']); previous_env = env_path.read_text()
        (self.logdir / 'deploy.env').write_text(previous_env)
        proxy = None; stopped = False; changed = False; healthy = False
        try:
            proxy = self.logdir / 'proxy-original.json'
            self.gate()
            if n8n_running: self.run(['docker', 'stop', '-t', '45', n8n])
            self.wait_idle()
            self.run(['docker', 'stop', '-t', '60', app]); stopped = True
            backup = self.logdir / 'before.db'
            snapshot_report = snapshot(self.config['database'], backup)
            shutil.copytree(self.config['state_dir'], self.logdir / 'state-before')
            scratch = self.logdir / 'rehearsal'; scratch.mkdir()
            shutil.copy2(backup, scratch / 'speda.db')
            migration = self.migration(release['image_id'], scratch, backup)
            self.rehearse(release['image_id'], scratch)
            # Prove the retained image boots against the additive schema too.
            self.rehearse(old, scratch)
            if not verify(backup, scratch / 'speda.db')['preserved']:
                raise RuntimeError('Schema rehearsal altered existing rows')
            self.migration(release['image_id'], Path(self.config['database']).parent, backup)
            if not verify(backup, self.config['database'])['preserved']:
                raise DataPreservationFailure('Existing rows changed; writers remain stopped for reconciliation')
            atomic(env_path, image_env(previous_env, release['image_id'])); changed = True
            self.run(self.compose + ['up', '-d', '--no-deps', '--no-build', 'app'])
            self.health(app); self.authenticated_check()
            actual = self.run(['docker', 'inspect', app, '--format', '{{.Image}}'])
            if actual != release['image_id']: raise RuntimeError('Running image mismatch')
            healthy = True
            result = {**release, 'previous_image_id': old, 'database_replaced': False,
                'private_config_retained': True, 'snapshot': snapshot_report, 'migration': migration,
                'rollback_compatibility_rehearsed': True, 'orion_spark_started': False,
                'deployed_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
            atomic(self.root / 'current.json', json.dumps(result, indent=2) + '\n')
            (self.logdir / 'success.json').write_text(json.dumps(result, indent=2))
        except Exception as failure:
            if stopped and not isinstance(failure, DataPreservationFailure):
                # Keep the current DB and state, including any credential rotation.
                # Old-image compatibility was checked before applying live DDL.
                if changed:
                    atomic(env_path, previous_env)
                    self.run(self.compose + ['up', '-d', '--no-deps', '--no-build', 'app'])
                else:
                    self.run(['docker', 'start', app])
                self.health(app); healthy = True
            raise
        finally:
            if proxy and proxy.exists() and (healthy or not stopped):
                try:
                    if n8n_running: self.run(['docker', 'start', n8n])
                finally:
                    self.proxy(proxy)
        with urlopen(self.config['public_url'].rstrip('/') + '/health/ready', timeout=20) as response:
            if response.status != 200: raise RuntimeError('Public readiness failed after release')
        print(json.dumps({'commit': release['commit'], 'image_id': release['image_id'],
            'deployed': True, 'data_and_private_configuration_retained': True, 'orion_spark_started': False}))
        return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('upload')
    args = parser.parse_args()
    if os.geteuid() != 0: raise SystemExit('Use the installed host deployment command through sudo')
    # A lost SSH transport must not terminate a database/traffic transition.
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    os.umask(0o077)
    config = json.loads(Path('/opt/speda-operations/cicd/config.json').read_text())
    Deployment(config).deploy(args.upload)
