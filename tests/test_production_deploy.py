"""Deployment failures must preserve the live dataset, secrets and stopped watchdog."""
import hashlib
import json
from pathlib import Path
import sqlite3
from unittest.mock import patch
from urllib.parse import urlsplit
import pytest
from scripts.deploy_production import Deployment, image_env, manifest, verify_loaded_image


COMMIT = 'a' * 40
NEW = 'sha256:' + 'b' * 64
OLD = 'sha256:' + 'c' * 64


def test_upload_checksum_identity_and_path_are_checked(tmp_path):
    upload = tmp_path / ('speda-deploy-' + COMMIT + '-12-1'); upload.mkdir()
    image = upload / 'image.tar.gz'; image.write_bytes(b'fixture image')
    value = {'commit': COMMIT, 'image_id': NEW, 'archive_sha256': hashlib.sha256(image.read_bytes()).hexdigest()}
    (upload / 'release.json').write_text(json.dumps(value))
    assert manifest(upload, tmp_path) == value
    image.write_bytes(b'incomplete upload')
    with pytest.raises(ValueError, match='checksum'): manifest(upload, tmp_path)
    with pytest.raises(ValueError, match='direct children'): manifest(upload)


def test_only_the_image_changes_in_private_deployment_env():
    text = 'SPEDA_IMAGE=' + OLD + '\nSPEDA_ENV_FILE=/private/production.env\nSPEDA_STATE_DIR=/private/state\n'
    changed = image_env(text, NEW)
    assert changed.replace(NEW, OLD) == text
    with pytest.raises(ValueError): image_env(text + 'SPEDA_IMAGE=duplicate\n', NEW)


def test_loaded_image_accepts_store_id_normalization_but_checks_all_layers_and_config():
    import copy
    config = {'Env': ['PATH=/app/bin'], 'Cmd': ['python', '-m', 'app.supervisor'],
        'Labels': {'org.opencontainers.image.revision': COMMIT}, 'WorkingDir': '/app'}
    expected = {'config': config, 'rootfs': {'diff_ids': ['sha256:fixture-layer']}, 'os': 'linux', 'architecture': 'amd64'}
    actual = {'Id': NEW, 'Config': {**config, 'User': ''}, 'RootFS': {'Layers': ['sha256:fixture-layer']}, 'Os': 'linux', 'Architecture': 'amd64'}
    assert verify_loaded_image(expected, actual, COMMIT) == NEW
    for key in ('Env', 'Cmd', 'Entrypoint'):
        changed = copy.deepcopy(actual); changed['Config'][key] = ['unexpected']
        with pytest.raises(ValueError): verify_loaded_image(expected, changed, COMMIT)
    changed = copy.deepcopy(actual); changed['RootFS']['Layers'].append('sha256:other')
    with pytest.raises(ValueError): verify_loaded_image(expected, changed, COMMIT)
    with pytest.raises(ValueError): verify_loaded_image(expected, actual, 'd' * 40)


class Host(Deployment):
    def __init__(self, config, *, failure=None):
        super().__init__(config); self.calls = []; self.image = OLD; self.failure = failure; self.failed = False

    def run(self, argv):
        self.calls.append(argv)
        if argv[:3] == ['docker', 'inspect', 'app']:
            return 'true' if argv[-1] == '{{.State.Running}}' else self.image
        if argv[:3] == ['docker', 'inspect', 'n8n']: return 'true'
        if argv[:3] == ['docker', 'image', 'inspect']: return COMMIT
        if argv[:2] == ['systemctl', 'show']: return 'inactive'
        if argv[:2] == ['docker', 'compose']:
            self.image = next(x.split('=', 1)[1] for x in Path(self.config['deploy_env']).read_text().splitlines() if x.startswith('SPEDA_IMAGE='))
        return ''

    def load_image(self, upload, release): return release
    def check_backend(self): pass

    def health(self, name):
        if self.failure == 'health' and self.image == NEW and not self.failed:
            self.failed = True
            # A completed write must survive even a failed health check and rollback.
            with sqlite3.connect(self.config['database']) as db: db.execute("INSERT INTO evidence VALUES(2,'retained during rollback')")
            raise RuntimeError('candidate health failed')

    def gate(self):
        p = self.logdir / 'proxy-original.json'; p.write_text('{}'); return p

    def proxy(self, path): self.calls.append(['restore-proxy'])

    def migration(self, image, data, backup):
        self.calls.append(['migration', image, str(data)])
        return {'new_tables': [], 'add_columns': [], 'unsupported_columns': []}

    def rehearse(self, image, data):
        self.calls.append(['rehearse', image])
        if self.failure == 'rehearsal': raise RuntimeError('rehearsal failed')

    def authenticated_check(self): self.calls.append(['authenticated-check'])


@pytest.fixture
def host(tmp_path):
    db = tmp_path / 'speda.db'
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE evidence(id INTEGER PRIMARY KEY, body TEXT)')
        c.execute("INSERT INTO evidence VALUES(1,'original owner evidence')")
    state = tmp_path / 'state'; state.mkdir(); (state / 'configuration.json').write_text('{"credential":"fixture-private"}')
    env = tmp_path / 'deploy.env'; env.write_text('SPEDA_IMAGE=' + OLD + '\nSPEDA_ENV_FILE=/private/production.env\n')
    config = {'operations_dir': str(tmp_path / 'deployments'), 'database': str(db), 'state_dir': str(state),
        'deploy_env': str(env), 'compose_project': 'renovated', 'compose_files': ['/private/compose.yml'],
        'app_container': 'app', 'n8n_container': 'n8n', 'caddy_container': 'caddy', 'public_url': 'https://fixture.invalid'}
    Path(config['operations_dir']).mkdir()
    return config


@pytest.mark.parametrize('failure', [None, 'rehearsal', 'health'])
def test_success_and_failed_release_never_restore_over_current_data_or_config(host, tmp_path, failure):
    driver = Host(host, failure=failure)
    inode = Path(host['database']).stat().st_ino
    state = (Path(host['state_dir']) / 'configuration.json').read_bytes()
    class Ready:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
    release = {'commit': COMMIT, 'image_id': NEW}
    with patch('scripts.deploy_production.urlopen', return_value=Ready()):
        if failure:
            with pytest.raises(RuntimeError): driver.locked_deploy(tmp_path, release)
        else:
            assert driver.locked_deploy(tmp_path, release)['rollback_compatibility_rehearsed']
    assert Path(host['database']).stat().st_ino == inode
    assert (Path(host['state_dir']) / 'configuration.json').read_bytes() == state
    with sqlite3.connect(host['database']) as db:
        assert db.execute('SELECT body FROM evidence WHERE id=1').fetchone()[0] == 'original owner evidence'
        if failure == 'health': assert db.execute('SELECT count(*) FROM evidence').fetchone()[0] == 2
    assert driver.image == (OLD if failure else NEW)
    assert ['restore-proxy'] in driver.calls
    assert not any(call[:2] == ['systemctl', 'start'] for call in driver.calls)
    assert (driver.logdir / 'before.db').is_file()


def test_live_workers_and_paid_calls_hold_deployment(host):
    driver = Host(host)
    assert driver.idle()
    with sqlite3.connect(host['database']) as db:
        db.execute('CREATE TABLE global_spending_calls(id TEXT,status TEXT)')
        db.execute("INSERT INTO global_spending_calls VALUES('active','started')")
    assert not driver.idle()


def test_release_driver_refuses_mismatched_runtime_database(host):
    from scripts.deploy_postgres import PostgresDeployment

    host['postgres'] = {'container': 'postgres', 'database': 'speda'}
    with patch('scripts.deploy_production.container_database',
               return_value=urlsplit('postgresql+asyncpg://owner@postgres/speda')):
        with pytest.raises(RuntimeError, match='SQLite deployment configuration'):
            Deployment(host).check_backend()
    with patch('scripts.deploy_postgres.container_database',
               return_value=urlsplit('sqlite+aiosqlite:////data/speda.db')):
        with pytest.raises(RuntimeError, match='PostgreSQL deployment configuration'):
            PostgresDeployment(host).check_backend()
    with patch('scripts.deploy_postgres.container_database',
               return_value=urlsplit('postgresql+asyncpg://owner@postgres/speda')):
        PostgresDeployment(host).check_backend()


def test_proxy_gate_changes_only_the_owned_hostname(host):
    driver = Deployment(host); driver.logdir = Path(host['operations_dir'])
    original = {'apps': {'http': {'servers': {'server': {'routes': [
        {'match': [{'host': ['fixture.invalid']}], 'handle': [{'handler': 'reverse_proxy'}]},
        {'match': [{'host': ['other.invalid']}], 'handle': [{'handler': 'reverse_proxy'}]}]}}}}}
    driver.run = lambda args: json.dumps(original)
    seen = []; driver.proxy = lambda path: seen.append(json.loads(path.read_text()))
    retained = driver.gate()
    routes = seen[0]['apps']['http']['servers']['server']['routes']
    assert routes[0]['handle'][0]['status_code'] == 503
    assert routes[1] == original['apps']['http']['servers']['server']['routes'][1]
    assert json.loads(retained.read_text()) == original
