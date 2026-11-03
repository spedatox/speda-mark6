"""Start/check the owner's isolated local test environment without exposing keys."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
DATA = Path.home() / '.codex' / 'local-testing' / 'renovated-mark-vi'


def environment():
    path = DATA / 'local.env'
    if not path.exists():
        raise SystemExit(f'Local test configuration is missing: {path}')
    env = {**os.environ, **{k: v for k, v in dotenv_values(path).items() if v is not None}}
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env['VITE_DEFAULT_MODEL'] = env.get('LLM_MAIN_MODEL', '')
    # Local projects need durable, disjoint roots even when coding Cells are off.
    # Never change production defaults or silently enable subprocess execution.
    if not env.get('PROJECT_WORKSPACE_ROOT') and not env.get('FORGE_WORKSPACE_ROOT'):
        env['PROJECT_WORKSPACE_ROOT'] = str(DATA / 'workspaces')
    if not env.get('PROJECT_STATE_ROOT'):
        env['PROJECT_STATE_ROOT'] = str(DATA / 'project-state')
    Path(env['PROJECT_WORKSPACE_ROOT'] if env.get('PROJECT_WORKSPACE_ROOT') else env['FORGE_WORKSPACE_ROOT']).mkdir(parents=True, exist_ok=True)
    Path(env['PROJECT_STATE_ROOT']).mkdir(parents=True, exist_ok=True)
    return env


def reachable(url, headers=None):
    try:
        return httpx.get(url, headers=headers, timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


def start():
    env = environment()
    DATA.mkdir(parents=True, exist_ok=True)
    backend = 'http://127.0.0.1:' + env['HTTP_PORT']
    frontend = 'http://127.0.0.1:5273'
    checks = [('backend', backend + '/health/ready',
               [sys.executable, '-m', 'app.supervisor']),
              ('frontend', frontend,
               [shutil.which('node') or 'node', str(ROOT / 'node_modules/vite/bin/vite.js'),
                '--config', str(ROOT / 'packages/heartbreaker/vite.web.config.ts'),
                '--host', '127.0.0.1', '--port', '5273', '--strictPort'])]
    for name, url, argv in checks:
        if reachable(url):
            if name == 'backend' and not reachable(backend + '/config', {'X-API-Key': env['SPEDA_API_KEY']}):
                raise SystemExit('Backend port belongs to another instance; no process was changed.')
            print(f'{name}: already running')
            continue
        with (DATA / f'{name}.log').open('ab') as log:
            process = subprocess.Popen(argv, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        (DATA / f'{name}.pid').write_text(str(process.pid), encoding='utf-8')
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if reachable(url):
                print(f'{name}: ready')
                break
            if process.poll() is not None:
                raise SystemExit(f'{name} stopped; inspect {DATA / (name + ".log")}')
            time.sleep(.5)
        else:
            raise SystemExit(f'{name} did not become ready; inspect {DATA / (name + ".log")}')
    status()


def status():
    env = environment()
    base = 'http://127.0.0.1:' + env['HTTP_PORT']
    print(json.dumps({'backend_ready': reachable(base + '/health/ready'),
        'authenticated': reachable(base + '/config', {'X-API-Key': env['SPEDA_API_KEY']}),
        'app': 'http://127.0.0.1:5273', 'app_ready': reachable('http://127.0.0.1:5273'),
        'data_directory': str(DATA)}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'status'], nargs='?', default='start')
    args = parser.parse_args()
    start() if args.action == 'start' else status()
