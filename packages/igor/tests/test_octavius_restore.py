# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Exercise the restore lifecycle using real SQLite files and a fake host."""

import json
import sqlite3

import pytest

from app.services import octavius_restore_host as worker
from app.services.octavius_restore import OctaviusRestore


def database(path, value):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE messages (body TEXT)")
    conn.execute("INSERT INTO messages VALUES (?)", (value,))
    conn.commit()
    conn.close()


def body(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT body FROM messages").fetchone()[0]
    finally:
        conn.close()


@pytest.fixture
def job(tmp_path, monkeypatch):
    root = tmp_path / "jobs"
    root.mkdir()
    (root / "active").mkdir()
    folder = root / "abc"
    folder.mkdir()
    live = tmp_path / "speda.db"
    database(live, "current")
    database(folder / "restored.db", "backup")
    worker.write_state(folder / "job.json", {
        "database": str(live), "container": "igor",
        "sha256": worker.digest(folder / "restored.db"),
    })
    worker.write_state(folder / "state.json", {"phase": "queued"})
    monkeypatch.setattr(worker.time, "sleep", lambda _: None)
    calls = []

    def docker(*args):
        calls.append(args)
        if args[0] == "inspect":
            return "false"
        return ""

    monkeypatch.setattr(worker, "docker", docker)
    monkeypatch.setattr(worker, "healthy", lambda _: True)
    return folder, live, calls


def state(folder):
    return json.loads((folder / "state.json").read_text())


def test_restore_stops_before_swap_preserves_old_journals_and_restarts(job, monkeypatch):
    folder, live, calls = job
    # Stand-ins for stale sidecars: never present beside the installed snapshot.
    live.with_name("speda.db-wal").write_bytes(b"old journal")
    live.with_name("speda.db-shm").write_bytes(b"old shared memory")

    def check_health(_):
        assert calls[0][0] == "stop"
        assert body(live) == "backup"
        assert not live.with_name("speda.db-wal").exists()
        assert (folder / "before-restore" / "speda.db-wal").read_bytes() == b"old journal"
        return True

    monkeypatch.setattr(worker, "healthy", check_health)
    worker.run_job(folder / "job.json")
    assert state(folder)["phase"] == "complete"
    assert body(folder / "before-restore" / "speda.db") == "current"
    assert not (folder.parent / "active").exists()


def test_corrupt_staging_never_stops_igor(job):
    folder, live, calls = job
    (folder / "restored.db").write_bytes(b"tampered")
    worker.run_job(folder / "job.json")
    assert state(folder)["phase"] == "failed"
    assert calls == []
    assert body(live) == "current"


def test_stop_that_does_not_stop_never_swaps(job, monkeypatch):
    folder, live, calls = job
    original = worker.docker
    monkeypatch.setattr(worker, "docker", lambda *args: "true" if args[0] == "inspect" else original(*args))
    worker.run_job(folder / "job.json")
    assert state(folder)["phase"] == "failed"
    assert body(live) == "current"


def test_unhealthy_restored_app_rolls_back(job, monkeypatch):
    folder, live, calls = job
    checks = iter([False, True])
    monkeypatch.setattr(worker, "healthy", lambda _: next(checks))
    worker.run_job(folder / "job.json")
    assert state(folder)["phase"] == "failed"
    assert state(folder)["rolled_back"] is True
    assert body(live) == "current"
    assert body(folder / "failed-restore" / "speda.db") == "backup"
    assert [c[0] for c in calls] == ["stop", "inspect", "start", "stop", "inspect", "start"]


def test_failed_rollback_retains_lock_and_reports_recovery(job, monkeypatch):
    folder, live, calls = job
    monkeypatch.setattr(worker, "healthy", lambda _: False)
    worker.run_job(folder / "job.json")
    assert state(folder)["phase"] == "recovery_required"
    assert (folder.parent / "active").exists()
    assert body(live) == "current"


def test_partial_move_failure_restores_original_files(job, monkeypatch):
    folder, live, calls = job
    live.with_name("speda.db-wal").write_bytes(b"old journal")
    replace = worker.os.replace

    def fail_wal_once(source, target):
        if str(source).endswith("speda.db-wal") and "before-restore" in str(target):
            raise OSError("simulated filesystem failure")
        return replace(source, target)

    monkeypatch.setattr(worker.os, "replace", fail_wal_once)
    worker.run_job(folder / "job.json")
    assert state(folder)["rolled_back"] is True
    assert body(live) == "current"


@pytest.fixture
def coordinator(tmp_path, monkeypatch):
    from app.services import octavius, host_bridge
    monkeypatch.setattr(octavius.settings, "octavius_protocol_enabled", True)
    service = OctaviusRestore(tmp_path)

    async def layout():
        return {"container": "igor", "host_data": tmp_path, "database": str(tmp_path / "speda.db")}

    async def stage(file_id, target, **kwargs):
        assert kwargs["require_hash"]
        database(target, "backup")
        return True, {"name": "chosen.db.gz"}

    calls = []

    async def run(command, **kwargs):
        calls.append(command)
        if command.startswith("sha256sum"):
            latest = service.root / (service.root / "latest").read_text()
            return 0, worker.digest(latest / "restored.db") + "  restored.db", ""
        return 0, "", ""

    monkeypatch.setattr(service, "layout", layout)
    monkeypatch.setattr(octavius, "stage_backup", stage)
    monkeypatch.setattr(host_bridge, "run", run)
    return service, calls


async def test_dispatch_is_host_owned_and_status_survives_a_new_controller(coordinator):
    service, calls = coordinator
    result = await service.start("selected-id")
    assert result["ok"] and result["phase"] == "queued"
    assert result["file_id"] == "selected-id"
    assert "systemd-run" in calls[-1]
    assert "docker stop" not in calls[-1]
    assert OctaviusRestore(service.data_dir).status()["job_id"] == result["job_id"]
    again = await service.start("selected-id")
    assert not again["ok"]
    assert len(calls) == 2


async def test_request_id_is_idempotent_even_after_completion(coordinator):
    service, calls = coordinator
    job_id = "a" * 32
    first = await service.start("id", job_id)
    assert (await service.start("id", job_id))["job_id"] == first["job_id"]
    saved = service.status(job_id)
    saved["phase"] = "complete"
    worker.write_state(service.root / job_id / "state.json", saved)
    (service.root / "active").rmdir()
    assert (await service.start("id", job_id))["phase"] == "complete"
    assert not (await service.start("different", job_id))["ok"]
    assert len(calls) == 2


def test_unknown_request_never_reports_previous_success(coordinator):
    service, _ = coordinator
    assert service.status("b" * 32)["phase"] == "failed"


async def test_download_failure_releases_lock_without_dispatch(coordinator, monkeypatch):
    from app.services import octavius
    service, calls = coordinator

    async def corrupt(*args, **kwargs):
        return False, "corrupt backup"

    monkeypatch.setattr(octavius, "stage_backup", corrupt)
    result = await service.start("id")
    assert not result["ok"] and "corrupt" in result["error"]
    assert not (service.root / "active").exists()
    assert calls == []


async def test_ambiguous_dispatch_never_releases_lock(coordinator, monkeypatch):
    from app.services import host_bridge
    service, calls = coordinator
    original = host_bridge.run

    async def lost(command, **kwargs):
        if command.startswith("systemd-run"):
            return 255, "", "SSH connection lost"
        return await original(command, **kwargs)

    monkeypatch.setattr(host_bridge, "run", lost)
    result = await service.start("id")
    assert not result["ok"] and result["phase"] == "queued"
    assert (service.root / "active").exists()
    assert "SSH connection lost" in service.status()["error"]


async def test_layout_discovers_actual_host_mount_not_a_guessed_path(tmp_path, monkeypatch):
    from app.services import host_bridge, octavius

    service = OctaviusRestore(tmp_path)
    monkeypatch.setattr(host_bridge, "remote_enabled", lambda: True)
    monkeypatch.setattr(octavius, "db_path", lambda: tmp_path / "brain.db")
    mounted = {"Id": "exact-igor-container", "Config": {"Labels": {"com.docker.compose.service": "app"}},
               "Mounts": [{"Type": "bind", "Destination": str(tmp_path), "Source": str(tmp_path / "host"), "RW": True}]}

    async def inspect(*args, **kwargs):
        return 0, json.dumps([mounted]), ""

    monkeypatch.setattr(host_bridge, "run", inspect)
    layout = await service.layout()
    assert layout["database"] == str(tmp_path / "host" / "brain.db")
    assert layout["container"] == "exact-igor-container"
    mounted["Config"]["Labels"]["com.docker.compose.service"] = "n8n"
    with pytest.raises(ValueError, match="not the Igor"):
        await service.layout()


async def test_unconfigured_host_bridge_refuses_restore_before_download(tmp_path, monkeypatch):
    from app.services import host_bridge, octavius

    service = OctaviusRestore(tmp_path)
    monkeypatch.setattr(host_bridge, "remote_enabled", lambda: False)
    monkeypatch.setattr(octavius.settings, "octavius_protocol_enabled", True)
    result = await service.start("id")
    assert not result["ok"] and "SSH host bridge" in result["error"]
    assert not (service.root / "active").exists()


async def test_restore_requires_api_key_and_explicit_backup_selection(coordinator):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from app.middleware.auth import AuthMiddleware
    from app.routers.octavius import router
    from app.config import settings

    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.include_router(router)
    app.state.octavius_restore = coordinator[0]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/admin/octavius/restore")).status_code == 401
        assert (await client.post("/admin/octavius/restore", json={"file_id": "id"})).status_code == 401
        headers = {"X-API-Key": settings.speda_api_key}
        assert (await client.post("/admin/octavius/restore", json={}, headers=headers)).status_code == 422
        result = await client.post("/admin/octavius/restore", json={"file_id": "id"}, headers=headers)
        assert result.status_code == 200 and result.json()["ok"]
