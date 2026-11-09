"""Disposable backend children only; never connects to an existing backend."""
from contextlib import ExitStack, closing
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time
import httpx


def test_managed_child_restart_persists_settings_and_changes_instance(tmp_path):
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',0))
        port=probe.getsockname()[1]
    env={**os.environ,'SPEDA_DATA_DIR':str(tmp_path/'state'),
        'DATABASE_URL':'sqlite+aiosqlite:///'+(tmp_path/'fixture.db').as_posix(),
        'HTTP_HOST':'127.0.0.1','HTTP_PORT':str(port),'SPEDA_MANAGED_PARENT':'1',
        'EXTERNAL_INTEGRATIONS_ENABLED':'false','MEMORY_CAPTURE_ENABLED':'false',
        'TEMP_OUTPUTS_DIR':str(tmp_path/'outputs'),'SPEDA_API_KEY':'process-fixture-key',
        'LLM_MAIN_MODEL':'','LLM_BACKGROUND_MODEL':'','PYTHONDONTWRITEBYTECODE':'1'}
    log=open(tmp_path/'backend.log','wb')
    child=None
    client=httpx.Client(base_url=f'http://127.0.0.1:{port}',headers={'X-API-Key':'process-fixture-key'},timeout=2,trust_env=False)
    def launch():
        return subprocess.Popen([sys.executable,'-m','app.server'],cwd=Path(__file__).resolve().parents[1],
            env=env,stdout=log,stderr=subprocess.STDOUT)
    def ready():
        deadline=time.monotonic()+35
        while time.monotonic()<deadline:
            assert child.poll() is None, 'Disposable backend exited; inspect its fixture log.'
            try:
                result=client.get('/health/ready')
                if result.status_code==200: return result.json()['instance_id']
            except httpx.HTTPError: pass
            time.sleep(.15)
        raise AssertionError('Disposable backend did not become ready')
    try:
        child=launch(); first=ready()
        snapshot=client.get('/config').json()
        response=client.put('/config',json={'expected_revision':snapshot['revision'],'request_id':'persist',
            'values':{'owner_timezone':'UTC','news_quota_digest':17,
                      'optimus_github_workspace_token':'fixture-github-token'}})
        assert response.status_code==200,response.text
        revision=response.json()['revision']
        assert response.json()['restart_required']
        restart={'expected_revision':revision,'request_id':'restart-fixture'}
        room = client.post('/coordination/rooms', json={'title': 'Restart fixture', 'lead': 'optimus'})
        assert room.status_code == 201, room.text
        room_id = room.json()['id']
        with closing(sqlite3.connect(tmp_path/'fixture.db')) as db:
            session_id, = db.execute('SELECT session_id FROM coord_rooms WHERE id = ?', (room_id,)).fetchone()
        # These read-only subscriptions stay open while the owner uses Settings.
        with ExitStack() as streams:
            for path in (f"/coordination/rooms/{room_id}/stream",
                         f"/legion/sessions/{session_id}/attach"):
                response = streams.enter_context(client.stream('GET', path))
                assert response.status_code == 200
            assert client.post('/config/restart',json=restart).status_code==202
            assert child.wait(timeout=8)==75
        child=launch(); second=ready()
        assert first!=second
        fresh=client.get('/config').json()
        assert fresh['revision']==revision and not fresh['restart_required']
        fields={f['key']:f for g in fresh['groups'] for f in g['fields']}
        assert fields['owner_timezone']['value']=='UTC' and fields['news_quota_digest']['value']==17
        assert fields['optimus_github_workspace_token']['is_set']
        # A lost-response retry after boot must not start another restart.
        result=client.post('/config/restart',json=restart)
        assert result.status_code==202 and result.json()['status']=='already_restarted'
        assert ready()==second
        assert client.post('/config/restart',json={**restart,'request_id':'finish-fixture'}).status_code==202
        assert child.wait(timeout=35)==75
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            try: child.wait(timeout=5)
            except subprocess.TimeoutExpired: child.kill(); child.wait()
        client.close(); log.close()
