"""Explicit Docker recovery, fenced against a surviving cooperative supervisor.

The OS releases the execution lock on process death. It does not release the
workshop claim: only verified Cell removal and a failed outcome do that.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def execution_lock(store, job_id):
    directory = store.path.parent / "execution-locks"
    directory.mkdir(exist_ok=True)
    if directory.is_symlink():
        raise ValueError("Execution lock directory must not be a symlink")
    path = directory / hashlib.sha256(job_id.encode()).hexdigest()
    if path.is_symlink():
        raise ValueError("Execution lock must not be a symlink")
    # Never unlink these files: another process could still hold the old inode.
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise ValueError("Execution supervisor is still active; interrupt it first") from exc
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise ValueError("Execution supervisor is still active; interrupt it first") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def docker(*args):
    try:
        return subprocess.run(["docker", *args], check=True, capture_output=True,
                              text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("Docker verification failed; execution claim retained") from exc


def record_cell(store, job_id, cell):
    from forge.cell.docker_cell import DockerCell
    if isinstance(cell, DockerCell):
        daemon = docker("info", "--format", "{{.ID}}")
        if not daemon:
            raise ValueError("Docker daemon identity unavailable")
        store.record_runtime(job_id, {"version": 1, "backend": "docker",
                                      "container": cell.container, "daemon": daemon})
    else:
        store.record_runtime(job_id, {"version": 1, "backend": "unsupported"})


def reconcile(store, job_id, note):
    if not isinstance(note, str) or not note.strip() or len(note) > 8000:
        raise ValueError("Recovery requires 1–8000 characters of context about partial work")
    with execution_lock(store, job_id):
        run = store.run_status(job_id)
        if run["status"] not in {"running", "interrupted"}:
            return run  # safe retry after a committed reconciliation
        runtime = json.loads(run.get("runtime", "{}"))
        if runtime.get("version") != 1 or runtime.get("backend") != "docker":
            raise ValueError("Legacy or non-Docker run requires operator verification; claim retained")
        if docker("info", "--format", "{{.ID}}") != runtime["daemon"]:
            raise ValueError("Docker daemon identity changed; claim retained")
        # A successful inventory distinguishes absence from inspect/daemon failure.
        ids = docker("ps", "-aq", "--no-trunc").split()
        containers = json.loads(docker("inspect", *ids)) if ids else []
        target = None
        workspace = Path(run["path"]).resolve()
        for cell in containers:
            if cell["Name"] == "/" + runtime["container"]:
                target = cell
            for mount in cell.get("Mounts", []):
                if mount.get("Type") != "bind" or mount.get("Destination") != "/workspace":
                    continue
                source = Path(mount["Source"]).resolve()
                if source == workspace or source in workspace.parents or workspace in source.parents:
                    state = cell["State"]
                    if (state.get("Status") not in {"exited", "dead"} or state.get("Running")
                            or state.get("Restarting") or state.get("Paused") or state.get("Pid") != 0):
                        raise ValueError("An overlapping Cell is still active or uncertain; claim retained")
        if target is None and not runtime.get("removal_target"):
            # A killed `docker run` client may have left an in-flight daemon
            # create request. Absence alone cannot prove it will stay absent.
            raise ValueError("Cell is missing without verified removal evidence; operator verification required")
        if target is not None:
            if runtime.get("removal_target") not in {None, target["Id"]}:
                raise ValueError("Cell identity changed during recovery; claim retained")
            state = target["State"]
            if (state.get("Status") not in {"exited", "dead"} or state.get("Running")
                    or state.get("Restarting") or state.get("Paused") or state.get("Pid") != 0):
                raise ValueError("Execution Cell is still active or uncertain; claim retained")
            if not any(m.get("Type") == "bind" and m.get("Destination") == "/workspace"
                       and Path(m["Source"]).resolve() == workspace for m in target.get("Mounts", [])):
                raise ValueError("Execution Cell workspace does not match; claim retained")
            # Persist the exact observed stopped identity before removal so a
            # lost reply or failed final commit can be retried safely.
            runtime["removal_target"] = target["Id"]
            store.record_runtime(job_id, runtime)
            # No force: a concurrently restarted container must cause refusal.
            docker("rm", target["Id"])
            if target["Id"] in docker("ps", "-aq", "--no-trunc").split():
                raise ValueError("Cell removal could not be verified; claim retained")
        evidence = (f"Verified abandoned Docker execution on daemon {runtime['daemon']}; "
                    f"Cell {runtime['container']} removed or absent; supervisor lock acquired. ")
        store.finish(job_id, "failed", evidence + note.strip())
        return store.run_status(job_id)
