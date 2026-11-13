import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace


def broker(tmp_path, monkeypatch):
    # Receipt/worker tests are portable; actual systemd/socket integration needs Linux.
    if sys.platform == 'win32':
        monkeypatch.setitem(sys.modules, 'fcntl', SimpleNamespace())
        import socketserver
        monkeypatch.setattr(socketserver, 'UnixStreamServer', socketserver.TCPServer, raising=False)
    spec = importlib.util.spec_from_file_location('host_broker_test', Path(__file__).parents[1] / 'scripts/orion_host_ops.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = tmp_path
    module.initialize_receipts()
    return module


def admitted(module, identity='a'*32):
    request = {'operation_id':identity, 'action':'exec', 'command':'echo done'}
    with sqlite3.connect(module.ROOT / 'receipts.sqlite3') as db:
        db.execute('INSERT INTO operations VALUES (?, ?, ?)', (identity, module.canonical_hash(request), json.dumps({'status':'started'})))
        db.execute('INSERT INTO operation_workers VALUES (?, ?, 0)', (identity, json.dumps(request)))
    return identity


def test_worker_persists_terminal_receipt_and_outbox_without_broker_thread(tmp_path, monkeypatch):
    module = broker(tmp_path, monkeypatch)
    identity = admitted(module)
    effects = []
    def command(request):
        effects.append(request)
        return {'status':'ok', 'stdout':'done', 'exit_code':0}
    module.host_command = command
    module.supervised_operation(identity)
    module.supervised_operation(identity)
    assert len(effects) == 1
    with sqlite3.connect(tmp_path / 'receipts.sqlite3') as db:
        receipt = json.loads(db.execute('SELECT reply FROM operations').fetchone()[0])
        events = db.execute('SELECT payload FROM event_outbox').fetchall()
    assert receipt['stdout'] == 'done' and len(events) == 1
    event = json.loads(events[0][0])
    assert event['result'] == receipt
    assert event['result_hash'] == module.canonical_hash(receipt)


def test_lost_worker_evidence_never_reexecutes_command(tmp_path, monkeypatch):
    module = broker(tmp_path, monkeypatch)
    identity = admitted(module)
    with sqlite3.connect(tmp_path / 'receipts.sqlite3') as db:
        db.execute('UPDATE operation_workers SET launched=1')
    def forbidden(_):
        raise AssertionError('Unknown side effect was replayed')
    module.host_command = forbidden
    module.supervised_operation(identity)
    assert module.operation_status(identity)['status'] == 'started'


def test_result_transaction_rolls_back_if_outbox_write_fails(tmp_path, monkeypatch):
    module = broker(tmp_path, monkeypatch)
    identity = admitted(module)
    with sqlite3.connect(tmp_path / 'receipts.sqlite3') as db:
        db.execute("CREATE TRIGGER reject_event BEFORE INSERT ON event_outbox BEGIN SELECT RAISE(ABORT, 'disk fault'); END")
    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        module.save(identity, {'status':'ok'})
    assert module.operation_status(identity)['status'] == 'started'


def test_event_protocol_replays_same_event_until_backend_commits(tmp_path, monkeypatch):
    import socket
    import struct
    import threading
    module = broker(tmp_path, monkeypatch)
    if not hasattr(socket, 'SO_PEERCRED'):
        monkeypatch.setattr(socket, 'SO_PEERCRED', 17, raising=False)
    identity = admitted(module)
    module.save(identity, {'status':'ok', 'stdout':'done'})
    class Peer:
        def __init__(self, sock): self.sock = sock
        def getsockopt(self, *_): return struct.pack('3i', 1, 0, 0)
        def __getattr__(self, name): return getattr(self.sock, name)
    def receive(after, acknowledge):
        backend, host = socket.socketpair()
        backend.settimeout(3)
        def handle():
            with host:
                module.EventHandler(Peer(host), None, None)
        thread = threading.Thread(target=handle, daemon=True)
        thread.start()
        with backend, backend.makefile('rwb', buffering=0) as stream:
            stream.write(json.dumps({'schema_version':1, 'after':after}).encode()+b'\n')
            event = json.loads(stream.readline())
            if acknowledge:
                stream.write(json.dumps({'ack':event['sequence']}).encode()+b'\n')
                stream.readline()  # heartbeat follows the committed acknowledgement
        thread.join(3)
        return event
    first = receive(0, False)
    replay = receive(0, True)
    assert first == replay
    with sqlite3.connect(tmp_path / 'receipts.sqlite3') as db:
        assert db.execute('SELECT sequence FROM event_ack').fetchone()[0] == first['sequence']
