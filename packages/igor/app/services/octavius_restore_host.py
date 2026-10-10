# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Standalone restore worker, copied to the host and run by systemd.

Uses only the Python standard library. It must outlive the container it stops.
The previous database AND its journals remain together for automatic rollback.
"""

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path


def write_state(path: Path, state: dict) -> None:
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        json.dump(state, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def digest(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            checksum.update(chunk)
    return checksum.hexdigest()


def docker(*args: str) -> str:
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[:500] or f"docker exited {result.returncode}")
    return result.stdout.strip()


def stop(container: str) -> None:
    docker("stop", "--time", "60", container)
    if docker("inspect", "--format", "{{.State.Running}}", container) != "false":
        raise RuntimeError("Igor is still running; database replacement refused")


def healthy(container: str) -> bool:
    # Check the app inside its container; no public networking or secrets needed.
    for _ in range(60):
        try:
            docker("exec", container, "python", "-c",
                   "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)")
            return True
        except (RuntimeError, subprocess.TimeoutExpired):
            time.sleep(2)
    return False


def run_job(job_file: Path) -> None:
    job = json.loads(job_file.read_text(encoding="utf-8"))
    root = job_file.parent
    state_path = root / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    database = Path(job["database"])
    staged = root / "restored.db"
    rollback = root / "before-restore"
    container = job["container"]
    moved = []
    installed = False
    stopped = False
    release = True

    def update(phase: str, **extra) -> None:
        state.update(phase=phase, **extra)
        write_state(state_path, state)

    try:
        # Allow the initiating HTTP response to leave before stopping Igor.
        time.sleep(3)
        if digest(staged) != job["sha256"]:
            raise RuntimeError("Staged database changed after verification")
        conn = sqlite3.connect(f"{staged.as_uri()}?mode=ro", uri=True)
        try:
            if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise RuntimeError("Staged database failed its host integrity check")
        finally:
            conn.close()
        if not database.is_file() or database.is_symlink():
            raise RuntimeError("Live database is missing or is a symlink")
        metadata = database.stat()
        # All preparations that can fail happen before taking Igor offline.
        rollback.mkdir(mode=0o700)
        os.chmod(staged, metadata.st_mode & 0o777)
        if hasattr(os, "chown"):
            os.chown(staged, metadata.st_uid, metadata.st_gid)
        update("stopping")
        stop(container)
        stopped = True
        update("installing")
        for suffix in ("", "-wal", "-shm"):
            source = Path(str(database) + suffix)
            if source.exists():
                target = rollback / source.name
                os.replace(source, target)
                moved.append((source, target))
        os.replace(staged, database)
        installed = True
        update("restarting", rollback_path=str(rollback))
        docker("start", container)
        if not healthy(container):
            raise RuntimeError("Restored Igor did not become healthy; reverting")
        update("complete")
    except Exception as exc:
        error = str(exc)[:600]
        try:
            if moved:
                # Never put the old files back under a running restored engine.
                stop(container)
                failed = root / "failed-restore"
                failed.mkdir(mode=0o700)
                if installed:
                    for suffix in ("", "-wal", "-shm"):
                        source = Path(str(database) + suffix)
                        if source.exists():
                            os.replace(source, failed / source.name)
                for source, target in moved:
                    os.replace(target, source)
            # A stop timeout can still have stopped the container. Start is
            # idempotent and safe even when the original is already running.
            if stopped or state["phase"] == "stopping" or moved:
                docker("start", container)
                if not healthy(container):
                    raise RuntimeError("Original Igor did not become healthy")
            update("failed", error=error, rolled_back=bool(moved))
        except Exception as recovery:
            release = False
            update("recovery_required", error=f"{error}; rollback: {recovery}",
                   rollback_path=str(rollback))
    finally:
        if release:
            (root.parent / "active").rmdir()


if __name__ == "__main__":
    run_job(Path(sys.argv[1]))
