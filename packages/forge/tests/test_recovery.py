import asyncio
import json
import os
import subprocess
import sys

import pytest

from forge import recovery
from forge.config import ForgeSettings
from forge.model.scripted import ScriptedModel
from forge.runtime import ExecutionSpec, execute
from forge.workshop import Workshop


def abandoned(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = Workshop(tmp_path)
    store.claim(workspace, "abandoned", "partial edit")
    store.record_runtime("abandoned", {"version": 1, "backend": "docker",
                                       "container": "forge-cell-old", "daemon": "daemon-1"})
    return store, workspace


def cell(workspace, *, running=False, name="/forge-cell-old"):
    return {"Id": "a" * 64, "Name": name,
            "State": {"Status": "running" if running else "exited", "Running": running,
                      "Restarting": False, "Paused": False, "Pid": 123 if running else 0},
            "Mounts": [{"Type": "bind", "Source": str(workspace), "Destination": "/workspace"}]}


def daemon(monkeypatch, containers, *, fail_remove=False, identity="daemon-1"):
    removed = []

    def docker(*args):
        if args[0] == "info":
            return identity
        if args[0] == "ps":
            return "\n".join(c["Id"] for c in containers)
        if args[0] == "inspect":
            return json.dumps(containers)
        if args[0] == "rm":
            assert "-f" not in args
            if fail_remove:
                raise ValueError("Docker verification failed")
            removed.append(args[1])
            containers[:] = [c for c in containers if c["Id"] != args[1]]
            return args[1]
        raise AssertionError(args)

    monkeypatch.setattr(recovery, "docker", docker)
    return removed


@pytest.mark.parametrize("mode", ["running", "renamed", "overlapping", "daemon", "remove", "legacy", "subprocess", "missing"])
def test_uncertain_recovery_never_releases_claim(tmp_path, monkeypatch, mode):
    store, workspace = abandoned(tmp_path)
    containers = [cell(workspace, running=mode in {"running", "renamed"},
                       name="/renamed-by-operator" if mode == "renamed" else "/forge-cell-old")]
    if mode == "overlapping":
        containers.append(cell(workspace / "child", running=True, name="/other"))
    if mode == "missing":
        containers.clear()
    if mode in {"legacy", "subprocess"}:
        store.record_runtime("abandoned", {} if mode == "legacy" else {"version": 1, "backend": "unsupported"})
    daemon(monkeypatch, containers, fail_remove=mode == "remove",
           identity="wrong" if mode == "daemon" else "daemon-1")
    with pytest.raises(ValueError):
        recovery.reconcile(store, "abandoned", "Retain partial files; checks pending")
    assert store.run_status("abandoned")["status"] == "running"
    with pytest.raises(ValueError, match="claimed"):
        store.claim(workspace, "next", "do not overlap")


def test_recovery_preserves_files_and_allows_fresh_worker(tmp_path, monkeypatch):
    store, workspace = abandoned(tmp_path)
    (workspace / "partial.txt").write_text("keep this")
    removed = daemon(monkeypatch, [cell(workspace)])
    result = recovery.reconcile(store, "abandoned", "Partial edit retained; tests not yet run")
    assert result["status"] == "failed"
    assert removed
    assert (workspace / "partial.txt").read_text() == "keep this"
    assert recovery.reconcile(store, "abandoned", "retry")["report"] == result["report"]

    async def emit(_):
        pass

    next_run = asyncio.run(execute(
        ExecutionSpec(job_id="fresh", role="coder", task="inspect remaining work",
                      workspace=workspace, model_ref="scripted"),
        model=ScriptedModel([lambda _: ("Inspected", [])]), emit=emit,
        settings=ForgeSettings(workspace_root=tmp_path, cell_backend="subprocess")))
    assert next_run.status == "succeeded"
    assert store.run_status("fresh")["status"] == "succeeded"
    with pytest.raises(ValueError, match="already exists"):
        store.claim(workspace, "abandoned", "never replay")


def test_killed_supervisor_releases_os_lock_but_not_workshop_claim(tmp_path, monkeypatch):
    store, workspace = abandoned(tmp_path)
    daemon(monkeypatch, [cell(workspace)])
    script = """
import sys,time
from pathlib import Path
from forge.workshop import Workshop
from forge.recovery import execution_lock
with execution_lock(Workshop(Path(sys.argv[1])), 'abandoned'):
    print('locked', flush=True)
    time.sleep(60)
"""
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    process = subprocess.Popen([sys.executable, "-B", "-c", script, str(tmp_path)],
                               stdout=subprocess.PIPE, text=True, env=env)
    try:
        assert process.stdout.readline().strip() == "locked"
        with pytest.raises(ValueError, match="supervisor is still active"):
            recovery.reconcile(store, "abandoned", "Must refuse")
        assert store.run_status("abandoned")["status"] == "running"
        process.kill()
        process.wait(timeout=10)
        assert store.run_status("abandoned")["status"] == "running"
        assert recovery.reconcile(store, "abandoned", "Supervisor killed; partial work retained")["status"] == "failed"
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
        process.stdout.close()


def test_docker_failure_and_missing_note_fail_closed(tmp_path, monkeypatch):
    store, _ = abandoned(tmp_path)
    def unavailable(*_):
        raise ValueError("Docker verification failed")
    monkeypatch.setattr(recovery, "docker", unavailable)
    for note in ("", "inspect files later"):
        with pytest.raises(ValueError):
            recovery.reconcile(store, "abandoned", note)
        assert store.run_status("abandoned")["status"] == "running"


def test_failed_final_commit_retries_verified_removal(tmp_path, monkeypatch):
    store, workspace = abandoned(tmp_path)
    daemon(monkeypatch, [cell(workspace)])
    finish = store.finish
    def fail(*_):
        raise OSError("Storage unavailable")
    monkeypatch.setattr(store, "finish", fail)
    store.interrupt("abandoned", "cleanup uncertain")
    with pytest.raises(OSError):
        recovery.reconcile(store, "abandoned", "partial files retained")
    assert store.run_status("abandoned")["status"] == "interrupted"
    monkeypatch.setattr(store, "finish", finish)
    assert recovery.reconcile(store, "abandoned", "retry after storage repair")["status"] == "failed"


def test_operator_cli_cannot_bypass_live_supervisor_for_new_docker_runs(tmp_path, monkeypatch, capsys):
    from forge.workshop import main
    store, workspace = abandoned(tmp_path)
    daemon(monkeypatch, [cell(workspace)])
    monkeypatch.setattr(sys, "argv", ["workshop", "--root", str(tmp_path), "--job", "abandoned",
                                      "--reconcile", "--cell-stopped", "--note", "partial files retained"])
    with recovery.execution_lock(store, "abandoned"):
        with pytest.raises(ValueError, match="supervisor is still active"):
            main()
    assert store.run_status("abandoned")["status"] == "running"
    main()
    assert json.loads(capsys.readouterr().out)["status"] == "failed"
