# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Owner-requested restore coordination; the database swap runs on the host."""

import asyncio
import json
import shlex
import socket
from pathlib import Path
from uuid import uuid4

from app.config import _DATA_DIR
from app.services import host_bridge, octavius
from app.services.octavius_restore_host import digest, write_state


class OctaviusRestore:
    def __init__(self, data_dir: Path = _DATA_DIR):
        self.data_dir = data_dir
        self.root = data_dir / "restore" / "jobs"

    def status(self, job_id: str = "") -> dict:
        explicit = bool(job_id)
        try:
            job_id = job_id or (self.root / "latest").read_text(encoding="utf-8").strip()
            # Never treat a file's contents as an arbitrary filesystem path.
            if len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
                raise ValueError("Invalid restore job id")
            state = json.loads((self.root / job_id / "state.json").read_text(encoding="utf-8"))
            notice = self.root / job_id / "dispatch-error.txt"
            if state["phase"] == "queued" and notice.exists():
                state["error"] = notice.read_text(encoding="utf-8")
            return state
        except FileNotFoundError:
            if explicit:
                return {"job_id": job_id, "phase": "failed", "error": "No restore was recorded for this request"}
            return {"phase": "idle"}
        except (OSError, ValueError, KeyError, TypeError):
            return {"job_id": job_id, "phase": "recovery_required", "error": "Restore status could not be read"}

    async def layout(self) -> dict:
        if not host_bridge.remote_enabled():
            raise ValueError("Automatic restore requires the SSH host bridge on a Docker deployment")
        code, output, error = await host_bridge.run(
            f"command -v python3 >/dev/null && command -v systemd-run >/dev/null && "
            "python3 -c 'import sys; assert sys.version_info >= (3, 8)' && "
            f"docker inspect {shlex.quote(socket.gethostname())}")
        if code:
            raise ValueError(f"Host restore preflight failed: {error[:300]}")
        containers = json.loads(output)
        if len(containers) != 1:
            raise ValueError("Could not identify Igor's container")
        container = containers[0]
        if container.get("Config", {}).get("Labels", {}).get("com.docker.compose.service") != "app":
            raise ValueError("Restore target is not the Igor app service")
        database = octavius.db_path()
        if database is None:
            raise ValueError("Automatic restore requires a SQLite file database")
        relative = database.resolve().relative_to(self.data_dir.resolve())
        mount = next((m for m in container["Mounts"] if m["Type"] == "bind"
                      and m["Destination"] == str(self.data_dir)), None)
        if not mount or not mount.get("RW"):
            raise ValueError("Igor's data directory must be a writable host bind mount")
        return {"container": container["Id"], "host_data": Path(mount["Source"]),
                "database": str(Path(mount["Source"]) / relative)}

    async def start(self, file_id: str, job_id: str = "") -> dict:
        if job_id and (len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id)):
            return {"ok": False, "error": "Invalid restore job id"}
        if job_id and (self.root / job_id).exists():
            previous = self.status(job_id)
            if previous.get("file_id") != file_id:
                return {"ok": False, "error": "Restore job id already belongs to another backup"}
            return {"ok": previous["phase"] not in ("failed", "recovery_required"), **previous}
        if not octavius.settings.octavius_protocol_enabled:
            return {"ok": False, "error": "The Octavius Protocol is disabled"}
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            (self.root / "active").mkdir()
        except FileExistsError:
            return {"ok": False, "error": "A restore is already active or needs operator recovery"}
        dispatched = False
        job_id = job_id or uuid4().hex
        folder = self.root / job_id
        state = {"job_id": job_id, "file_id": file_id, "phase": "preparing"}
        try:
            folder.mkdir(mode=0o700)
            (self.root / "latest").write_text(job_id, encoding="utf-8")
            write_state(folder / "state.json", state)
            layout = await self.layout()
            ok, report = await octavius.stage_backup(file_id, folder / "restored.db", require_hash=True)
            if not ok:
                raise ValueError(report)
            state["name"] = report["name"]
            host_folder = layout["host_data"] / folder.relative_to(self.data_dir)
            worker = folder / "worker.py"
            worker.write_bytes(Path(__file__).with_name("octavius_restore_host.py").read_bytes())
            job = {"container": layout["container"], "database": layout["database"],
                   "sha256": await asyncio.to_thread(digest, folder / "restored.db")}
            write_state(folder / "job.json", job)
            # Prove host/container mount identity before dispatch, without guessing
            # /opt/speda or relying on the deployment repository's location.
            code, out, err = await host_bridge.run(
                f"sha256sum {shlex.quote(str(host_folder / 'restored.db'))}")
            if code or not out.split() or out.split()[0] != job["sha256"]:
                raise ValueError(f"Host could not verify the staged database: {err[:300]}")
            state["phase"] = "queued"
            write_state(folder / "state.json", state)
            # Once dispatch begins, an SSH failure cannot prove that the worker
            # did not start. Keep the lock; never resubmit an ambiguous restore.
            dispatched = True
            code, _, err = await host_bridge.run(
                f"systemd-run --quiet --collect --unit=speda-restore-{job_id} "
                f"--property=UMask=0077 --property=Type=exec -- python3 "
                f"{shlex.quote(str(host_folder / 'worker.py'))} "
                f"{shlex.quote(str(host_folder / 'job.json'))}")
            if code:
                error = f"Restore dispatch could not be confirmed. Check status before retrying: {err[:300]}"
                (folder / "dispatch-error.txt").write_text(error, encoding="utf-8")
                return {"ok": False, **state, "error": error}
            return {"ok": True, **state}
        except Exception as exc:
            if dispatched:
                # The host may already be installing. Do not overwrite its
                # state with a local error or make the request look retryable.
                return {"ok": False, **state, "error": f"Check host restore status: {str(exc)[:500]}"}
            state.update(phase="failed", error=str(exc)[:600])
            write_state(folder / "state.json", state)
            return {"ok": False, **state}
        finally:
            if not dispatched:
                (self.root / "active").rmdir()
