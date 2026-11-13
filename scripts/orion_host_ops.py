"""Host-owned System Ops broker. Install outside the application container."""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import socket
import socketserver
import sqlite3
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
from uuid import uuid4
from contextlib import closing

ROOT = Path("/opt/speda-operations/orion-host-ops")
SOCKET = ROOT / "socket/broker.sock"
DEPLOY = Path("/opt/speda-operations/cicd/deployments")
SCRIPT = "/usr/local/libexec/speda-orion-host-ops"
DOCKER = "/usr/bin/docker"
LIMIT = 128 * 1024
SERVICES = {
    "app": "speda-renovated-app-1", "n8n": "speda-n8n-1", "caddy": "speda-caddy-1",
    "sandbox": "speda-sandbox-1", "browser": "speda-browser-1",
    "playwright-mcp": "speda-playwright-mcp-1", "hisar": "speda-hisar-1",
}
HOST_COMMANDS = {"exec", "read_file", "write_file"}
DIAGNOSTICS = {
    "load": ["/usr/bin/uptime"],
    "storage": ["/usr/bin/df", "-h", "-x", "tmpfs", "-x", "devtmpfs"],
    "listeners": ["/usr/bin/ss", "-lntup"],
    "failed_units": ["/usr/bin/systemctl", "--failed", "--no-legend", "--plain", "--no-pager"],
    "timezone": ["/usr/bin/timedatectl", "show", "--property=Timezone", "--value"],
    "ssh_root_password_logins": [
        "/usr/bin/journalctl", "--no-pager", "--output=short-iso", "--since=24 hours ago",
        "--lines=20", "--identifier=sshd", "--identifier=sshd-session",
        "--grep=Accepted password for root ",
    ],
}
ACTIONS = HOST_COMMANDS | {"diagnostics", "service_logs", "restart_service"}
ID = re.compile(r"[a-f0-9]{32}")

# Run inside the live application container. The API credential stays there.
APP_RESTART = """
import json,sys,urllib.error,urllib.request
from app.config import settings
base=f'http://127.0.0.1:{settings.http_port}'
def request(path, body=None):
    req=urllib.request.Request(base+path, data=None if body is None else json.dumps(body).encode(),
        headers={'X-API-Key':settings.speda_api_key,'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=5) as reply:
        return json.load(reply)
try:
    if sys.argv[1]=='ready':
        print(json.dumps(request('/health/ready')))
    else:
        cfg=request('/config')
        body=request('/config/restart',{'expected_revision':cfg['revision'],'request_id':sys.argv[1]})
        print(json.dumps({'status':body['status'],'instance_id':body['instance_id']}))
except urllib.error.HTTPError as exc:
    try: code=json.load(exc).get('code')
    except ValueError: code=None
    print(json.dumps({'status':code if code in ('restart_busy','config_conflict') else 'request_failed'}))
"""


def result(status, **fields):
    return {"status": status, **fields}


def run(argv, timeout, payload=None):
    """Drain both streams without storing unbounded output."""
    deadline = None if timeout is None else time.monotonic() + timeout
    with tempfile.TemporaryFile() as input_file, selectors.DefaultSelector() as selector:
        if payload is not None:
            input_file.write(json.dumps(payload, ensure_ascii=False).encode() + b"\n")
            input_file.seek(0)
        with subprocess.Popen(argv, stdin=input_file, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, start_new_session=True) as process:
            output = {"stdout": bytearray(), "stderr": bytearray()}
            for key in output:
                selector.register(getattr(process, key), selectors.EVENT_READ, key)
            truncated = False
            try:
                while selector.get_map():
                    remaining = None if deadline is None else deadline - time.monotonic()
                    if remaining is not None and remaining <= 0:
                        raise TimeoutError
                    for key, _ in selector.select(remaining):
                        data = os.read(key.fd, 4096)
                        if not data:
                            selector.unregister(key.fileobj)
                            continue
                        buffer = output[key.data]
                        capacity = max(0, 8000 - len(buffer))
                        buffer.extend(data[:capacity])
                        truncated |= len(data) > capacity
                remaining = None if deadline is None else max(0.1, deadline - time.monotonic())
                code = process.wait(timeout=remaining)
            except (TimeoutError, subprocess.TimeoutExpired):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                return result("timeout", exit_code=process.returncode, truncated=truncated,
                    **{k:v.decode("utf-8", "replace") for k,v in output.items()})
            return result("ok" if code == 0 else "failed", exit_code=code,
                          truncated=truncated, **{k: v.decode("utf-8", "replace") for k, v in output.items()})


def worker():
    request = json.loads(sys.stdin.buffer.readline(LIMIT + 1))
    action = request["action"]
    if action == "exec":
        os.execv("/bin/bash", ["bash", "--noprofile", "--norc", "-c", request["command"]])
    path = Path(request["path"])
    if not path.is_absolute():
        raise ValueError("Use an absolute path on the host.")
    if action == "read_file":
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK), "rb") as stream:
            sys.stdout.buffer.write(stream.read(8001))
        return
    target = path.resolve()
    try:
        existing = target.stat()
    except FileNotFoundError:
        existing = None
    if existing is not None and not stat.S_ISREG(existing.st_mode):
        raise ValueError("write_file requires a regular file.")
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(request["content"].encode())
            stream.flush()
            if existing is not None:
                os.fchown(stream.fileno(), existing.st_uid, existing.st_gid)
                os.fchmod(stream.fileno(), stat.S_IMODE(existing.st_mode))
            os.fsync(stream.fileno())
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    print(f"Saved {target}.")


def host_command(request):
    seconds = request.get("timeout")
    unit = "speda-orion-command-" + request["operation_id"]
    argv = ["/usr/bin/systemd-run", "--pipe", "--wait", "--collect", "--quiet", "--service-type=exec",
            f"--unit={unit}", "--uid=root", "--gid=root", "--working-directory=/root",
            "--setenv=HOME=/root", "--setenv=LANG=C.UTF-8", "--property=UMask=0077",
            "--property=TasksMax=infinity"]
    if seconds is not None:
        argv.extend([f"--property=RuntimeMaxSec={seconds}s", "--property=TimeoutStopSec=2s"])
    reply = run([*argv, "/usr/bin/python3", SCRIPT, "worker"],
                None if seconds is None else seconds + 8, request)
    if reply["status"] == "timeout":
        run(["/usr/bin/systemctl", "stop", unit], 8)
    return reply


def service_logs(service):
    if service == "orion-spark":
        return run(["/usr/bin/journalctl", "--unit=orion-spark.service", "-n", "30", "--no-pager"], 10)
    return run([DOCKER, "logs", "--tail", "30", "--timestamps", SERVICES[service]], 10)


def app_restart(identity):
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        call = run([DOCKER, "exec", SERVICES["app"], "python", "-c", APP_RESTART, "orion-" + identity], 12)
        if call["status"] != "ok":
            return result("unknown", reason="The backend restart request could not be confirmed.")
        receipt = json.loads(call["stdout"])
        if receipt["status"] == "restarting":
            break
        if receipt["status"] == "already_restarted":
            return result("ok", service="app", instance_id=receipt["instance_id"])
        if receipt["status"] not in {"restart_busy", "config_conflict"}:
            return result("failed", reason="The backend declined the restart.")
        time.sleep(3)
    else:
        return result("busy", reason="The backend still had active work. No restart was admitted.")
    deadline = time.monotonic() + 75
    while time.monotonic() < deadline:
        call = run([DOCKER, "exec", SERVICES["app"], "python", "-c", APP_RESTART, "ready"], 12)
        if call["status"] == "ok":
            ready = json.loads(call["stdout"])
            if ready.get("status") == "ready" and ready["instance_id"] != receipt["instance_id"]:
                return result("ok", service="app", instance_id=ready["instance_id"])
        time.sleep(3)
    return result("unknown", reason="Restart was accepted. A new ready instance has not been confirmed.")


def restart(identity, service):
    with (DEPLOY / "deployment.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return result("busy", reason="A deployment or maintenance operation holds the lock.")
        if (DEPLOY / "orion-spark.inhibit").exists():
            return result("busy", reason="Production maintenance is in progress.")
        if service == "app":
            return app_restart(identity)
        if service == "orion-spark":
            stopped = run(["/usr/bin/systemctl", "restart", "orion-spark.service"], 40)
            if stopped["status"] == "ok":
                return run(["/usr/bin/systemctl", "is-active", "orion-spark.service"], 10)
            return stopped
        name = SERVICES[service]
        restarted = run([DOCKER, "restart", "--time", "20", name], 40)
        if restarted["status"] != "ok":
            return restarted
        deadline = time.monotonic() + 50
        while time.monotonic() < deadline:
            state = run([DOCKER, "inspect", "--format", "{{json .State}}", name], 8)
            if state["status"] == "ok":
                container = json.loads(state["stdout"])
                if container["Running"] and container.get("Health", {}).get("Status", "none") in {"healthy", "none"}:
                    return result("ok", service=service, state="running",
                                  health=container.get("Health", {}).get("Status"))
            time.sleep(2)
        return result("unknown", reason="Container restart was requested. Its ready state was not confirmed.")


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def save(identity, reply):
    with closing(sqlite3.connect(ROOT / "receipts.sqlite3", timeout=30)) as db, db:
        row = db.execute("SELECT fingerprint FROM operations WHERE id=?", (identity,)).fetchone()
        db.execute("UPDATE operations SET reply=? WHERE id=?", (json.dumps(reply), identity))
        if row and reply['status'] not in {'started', 'scheduled', 'running'}:
            version = db.execute("SELECT COUNT(*) FROM event_outbox WHERE json_extract(payload, '$.operation_id')=?", (identity,)).fetchone()[0] + 1
            event = dict(schema_version=1, executor='orion-host-v1', executor_instance=socket.gethostname(),
                event_id=uuid4().hex, operation_id=identity, fingerprint=row[0], state_version=version,
                result=reply, result_hash=canonical_hash(reply))
            db.execute("INSERT INTO event_outbox(event_id, payload) VALUES (?, ?)",
                       (event['event_id'], json.dumps(event)))


def supervised_operation(identity):
    with closing(sqlite3.connect(ROOT / "receipts.sqlite3", timeout=30)) as db, db:
        row = db.execute("SELECT request, launched FROM operation_workers WHERE id=?", (identity,)).fetchone()
        if not row or row[1]:
            return  # Never rerun an admitted worker after uncertain execution.
        db.execute("UPDATE operation_workers SET launched=1 WHERE id=?", (identity,))
        request = json.loads(row[0])
    try:
        action = request['action']
        if action in HOST_COMMANDS:
            reply = host_command(request)
        elif action == 'restart_service':
            reply = restart(identity, request['service'])
        elif action == 'service_logs':
            reply = service_logs(request['service'])
        else:
            reply = run(DIAGNOSTICS[request['diagnostic']], 10)
    except Exception:
        reply = result('unknown', reason='Worker result requires reconciliation.')
    save(identity, reply)


def launch_operation(request):
    identity = request['operation_id']
    unit = 'speda-orion-result-' + identity
    launch = run(['/usr/bin/systemd-run', '--quiet', '--service-type=exec',
        '--unit=' + unit, '--uid=root', '--gid=root', '--property=UMask=0077',
        '/usr/bin/python3', SCRIPT, 'operation', identity], 10)
    if launch['status'] != 'ok':
        state = run(['/usr/bin/systemctl', 'is-active', unit], 5)
        if state['status'] == 'ok':
            return
        if operation_status(identity)['status'] not in {'started', 'scheduled', 'running'}:
            return
        save(identity, result('unknown', reason='Worker launch could not be established; do not replay.'))


class EventHandler(socketserver.StreamRequestHandler):
    def handle(self):
        _, uid, _ = struct.unpack('3i', self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid != 0:
            return
        self.request.settimeout(15)
        try:
            subscription = json.loads(self.rfile.readline(LIMIT + 1))
            if subscription.get('schema_version') != 1 or type(subscription.get('after')) is not int:
                return
            after = subscription['after']
            while True:
                with closing(sqlite3.connect(ROOT / 'receipts.sqlite3', timeout=30)) as db:
                    rows = db.execute('SELECT sequence, payload FROM event_outbox WHERE sequence>? ORDER BY sequence LIMIT 100', (after,)).fetchall()
                for sequence, payload in rows:
                    event = {**json.loads(payload), 'sequence':sequence}
                    self.wfile.write(json.dumps(event).encode()+b'\n')
                    self.wfile.flush()
                    ack = json.loads(self.rfile.readline(LIMIT + 1))
                    if ack.get('ack') != sequence:
                        return
                    after = sequence
                    with closing(sqlite3.connect(ROOT / 'receipts.sqlite3', timeout=30)) as db, db:
                        db.execute('INSERT OR REPLACE INTO event_ack VALUES (1, ?)', (after,))
                if not rows:
                    self.wfile.write(b'{"type":"heartbeat"}\n')
                    self.wfile.flush()
                    time.sleep(1)
        except (OSError, ValueError, sqlite3.Error):
            return


class EventServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def serve_events():
    path = ROOT / 'socket/events.sock'
    path.unlink(missing_ok=True)
    with EventServer(str(path), EventHandler) as server:
        path.chmod(0o600)
        server.serve_forever()


def operation_status(identity):
    with closing(sqlite3.connect(ROOT / "receipts.sqlite3")) as db:
        row = db.execute("SELECT reply FROM operations WHERE id=?", (identity,)).fetchone()
    return json.loads(row[0]) if row else result("unavailable", reason="Operation not found.")


def admit(request):
    identity = request.get("operation_id")
    action = request.get("action")
    if not isinstance(identity, str) or not ID.fullmatch(identity):
        return result("denied", reason="Invalid operation ID.")
    if action == "operation_status":
        return operation_status(identity)
    if action not in ACTIONS:
        return result("denied", reason="Unsupported operation.")
    service = request.get("service")
    if action in {"service_logs", "restart_service"} and service not in {*SERVICES, "orion-spark"}:
        return result("denied", reason="Service is outside the maintenance scope.")
    diagnostic = request.get("diagnostic")
    if action == "diagnostics" and diagnostic not in DIAGNOSTICS:
        return result("denied", reason="Unknown host diagnostic.")
    if "timeout" in request and (type(request["timeout"]) is not int or request["timeout"] < 1):
        return result("denied", reason="Timeout must be a positive number of seconds.")
    for key in ("command", "path", "content"):
        if key in request and (not isinstance(request[key], str) or "\x00" in request[key]):
            return result("denied", reason=f"Invalid {key}.")
    required = {"exec": ("command",), "read_file": ("path",), "write_file": ("path", "content")}
    if any(key not in request for key in required.get(action, ())):
        return result("denied", reason="Missing operation arguments.")
    fingerprint = canonical_hash(request)
    initial = result("scheduled") if action == "restart_service" else result("started")
    with closing(sqlite3.connect(ROOT / "receipts.sqlite3")) as db, db:
        row = db.execute("SELECT fingerprint, reply FROM operations WHERE id=?", (identity,)).fetchone()
        if row:
            return json.loads(row[1]) if row[0] == fingerprint else result("denied", reason="Operation ID was reused.")
        db.execute("INSERT INTO operations VALUES (?, ?, ?)", (identity, fingerprint, json.dumps(initial)))
        db.execute("INSERT INTO operation_workers(id, request, launched) VALUES (?, ?, 0)", (identity, json.dumps(request)))
    print(json.dumps({"operation_id": identity, "action": action, "service": service}), flush=True)
    launch_operation(request)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        reply = operation_status(identity)
        if reply['status'] not in {'started', 'scheduled', 'running'}:
            return reply
        time.sleep(.05)
    return operation_status(identity)



class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        _, uid, _ = struct.unpack("3i", self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid != 0:
            return
        self.request.settimeout(5)
        try:
            line = self.rfile.readline(LIMIT + 1)
            if len(line) > LIMIT:
                self.wfile.write(json.dumps(result("denied", reason="Host request exceeds 128 KiB.")).encode() + b"\n")
                return
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError
            reply = admit(request)
        except (OSError, ValueError, TypeError, sqlite3.Error):
            reply = result("unknown", reason="The host broker did not confirm the request.")
        self.wfile.write(json.dumps(reply).encode() + b"\n")


def initialize_receipts():
    with closing(sqlite3.connect(ROOT / "receipts.sqlite3")) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, reply TEXT NOT NULL)")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE IF NOT EXISTS operation_workers(id TEXT PRIMARY KEY, request TEXT NOT NULL, launched INTEGER NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS event_outbox(sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS event_ack(id INTEGER PRIMARY KEY, sequence INTEGER NOT NULL)")


def serve():
    os.umask(0o077)
    initialize_receipts()
    # Inspect durable evidence. A missing unit cannot prove that an effect never ran.
    with closing(sqlite3.connect(ROOT / 'receipts.sqlite3')) as db:
        unfinished = db.execute("SELECT id FROM operations WHERE json_extract(reply, '$.status') IN ('started','scheduled','running')").fetchall()
    for (identity,) in unfinished:
        state = run(['/usr/bin/systemctl', 'is-active', 'speda-orion-result-' + identity], 5)
        if state['status'] != 'ok':
            save(identity, result('unknown', reason='No live worker or terminal receipt; reconciliation required.'))
    threading.Thread(target=serve_events, daemon=True).start()
    SOCKET.unlink(missing_ok=True)
    with socketserver.UnixStreamServer(str(SOCKET), Handler) as server:
        SOCKET.chmod(0o600)
        server.serve_forever()


if __name__ == "__main__":
    if sys.argv[1:] == ["worker"]:
        worker()
    elif len(sys.argv) == 3 and sys.argv[1] == "operation" and ID.fullmatch(sys.argv[2]):
        supervised_operation(sys.argv[2])
    elif sys.argv[1:] == ["serve"]:
        serve()
    else:
        raise SystemExit("Expected serve or worker.")
