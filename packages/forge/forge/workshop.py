"""Durable project handoffs and exclusive execution claims, not a scheduler.

The SQLite record lives at the workshop root, outside individual Cell mounts.
Claims never expire automatically: a dead supervisor may have left a live Cell.
An operator must reconcile that Cell before clearing an interrupted claim.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class Workshop:
    def __init__(self, root: Path, *, readonly: bool = False):
        self.root = root.resolve()
        self.readonly = readonly
        directory = self.root / ".forge"
        if directory.resolve() != directory:
            raise ValueError("Workshop state directory must not be a symlink")
        if not readonly:
            directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "workshop.sqlite3"
        if self.path.is_symlink():
            raise ValueError("Workshop database must not be a symlink")
        if readonly:
            if not self.path.is_file():
                raise FileNotFoundError("Workshop has not been initialized; use workshop_update to discover or select a project")
            return
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE,
                    revision INTEGER NOT NULL DEFAULT 0,
                    checkpoint TEXT NOT NULL DEFAULT '{}', updated TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                    status TEXT NOT NULL, task TEXT NOT NULL, report TEXT NOT NULL DEFAULT '',
                    started TEXT NOT NULL, finished TEXT,
                    FOREIGN KEY(project_id) REFERENCES projects(id)
                );
                CREATE INDEX IF NOT EXISTS runs_project ON runs(project_id, started);
            """)

    @contextmanager
    def _connect(self):
        db = (sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=10)
              if self.readonly else sqlite3.connect(self.path, timeout=10))
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def identity(workspace: Path) -> tuple[str, str]:
        path = os.path.normcase(str(workspace.resolve()))
        return hashlib.sha256(path.encode()).hexdigest()[:24], path

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _register(self, db, workspace: Path) -> str:
        if not workspace.is_dir():
            raise ValueError(f"Workspace is unavailable: {workspace}")
        candidate = workspace.resolve()
        if self.path.is_relative_to(candidate) or candidate.is_relative_to(self.path.parent):
            raise ValueError("Select an individual project, not the workshop root or its parent; "
                             "the execution database and coordinator metadata must stay outside the worker's mount")
        key, path = self.identity(workspace)
        db.execute("INSERT OR IGNORE INTO projects(id,path,updated) VALUES(?,?,?)",
                   (key, path, self._now()))
        return key

    def register(self, workspace: Path) -> dict:
        with self._connect() as db:
            key = self._register(db, workspace)
        return self.inspect(key)

    def inspect(self, key: str) -> dict:
        with self._connect() as db:
            row = db.execute("SELECT * FROM projects WHERE id=?", (key,)).fetchone()
            if row is None:
                raise ValueError("Unknown workshop project")
            result = dict(row)
            result["checkpoint"] = json.loads(result["checkpoint"])
            result["available"] = Path(result["path"]).is_dir()
            result["runs"] = [dict(r) for r in db.execute(
                "SELECT * FROM runs WHERE project_id=? ORDER BY started DESC LIMIT 5", (key,))]
            result["active_runs"] = [dict(r) for r in db.execute(
                "SELECT * FROM runs WHERE project_id=? AND status IN ('running','interrupted')", (key,))]
            return result

    def list_projects(self, *, limit: int = 100, offset: int = 0) -> dict:
        with self._connect() as db:
            total = db.execute("SELECT count(*) FROM projects").fetchone()[0]
            rows = db.execute("SELECT id,path,revision,updated FROM projects "
                              "ORDER BY updated DESC,id LIMIT ? OFFSET ?", (limit, offset)).fetchall()
            projects = []
            for row in rows:
                project = dict(row)
                project["available"] = Path(project["path"]).is_dir()
                active = db.execute("SELECT id FROM runs WHERE project_id=? AND status IN ('running','interrupted')",
                                    (project["id"],)).fetchall()
                project["active_run_ids"] = [r[0] for r in active]
                projects.append(project)
            return {"projects": projects, "total": total,
                    "next_offset": offset + limit if offset + limit < total else None}

    def checkpoint(self, workspace: Path, *, revision: int, state: dict) -> dict:
        allowed = {"name", "objective", "acceptance", "progress", "next_steps", "blockers", "checks", "status"}
        if set(state) - allowed:
            raise ValueError("Unknown checkpoint field")
        if state.get("status") not in {"active", "paused", "blocked", "complete"}:
            raise ValueError("Checkpoint needs an explicit project status")
        for key in ("name", "objective", "progress"):
            if not isinstance(state.get(key, ""), str):
                raise ValueError(f"{key} must be text")
        for key in ("acceptance", "next_steps", "blockers", "checks"):
            if not isinstance(state.get(key, []), list) or not all(
                isinstance(item, str) for item in state.get(key, [])
            ):
                raise ValueError(f"{key} must be a list of text entries")
        if not state.get("objective", "").strip():
            raise ValueError("Checkpoint needs the project's objective")
        if state["status"] == "complete" and (state.get("blockers") or state.get("next_steps")):
            raise ValueError("A complete project cannot have outstanding work or blockers")
        encoded = json.dumps(state, ensure_ascii=False)
        if len(encoded) > 16000:
            raise ValueError("Checkpoint exceeds 16000 characters")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            key = self._register(db, workspace)
            changed = db.execute("UPDATE projects SET checkpoint=?,revision=revision+1,updated=? "
                                 "WHERE id=? AND revision=?", (encoded, self._now(), key, revision))
            if changed.rowcount != 1:
                raise ValueError("Checkpoint changed; inspect it again before updating")
        return self.inspect(key)

    def claim(self, workspace: Path, job_id: str, task: str) -> dict:
        """Atomically refuse duplicate jobs and overlapping parent/child checkouts."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            key = self._register(db, workspace)
            candidate = Path(self.identity(workspace)[1])
            prior = db.execute("SELECT status FROM runs WHERE id=?", (job_id,)).fetchone()
            if prior:
                raise ValueError(f"Job {job_id} already exists ({prior[0]}); inspect its result, do not replay it")
            for run in db.execute("SELECT r.id,p.path FROM runs r JOIN projects p "
                                  "ON p.id=r.project_id WHERE r.status IN ('running','interrupted')"):
                other = Path(run["path"])
                if candidate == other or candidate in other.parents or other in candidate.parents:
                    raise ValueError(f"Workspace is claimed by {run['id']}. Inspect that run; "
                                     "after a restart reconcile its Cell before retrying")
            db.execute("INSERT INTO runs(id,project_id,status,task,started) VALUES(?,?,'running',?,?)",
                       (job_id, key, task[:16000], self._now()))
            db.execute("UPDATE projects SET updated=? WHERE id=?", (self._now(), key))
        return self.inspect(key)

    def finish(self, job_id: str, status: str, report: str) -> None:
        if status not in {"succeeded", "failed", "cancelled"}:
            raise ValueError("Invalid terminal status")
        with self._connect() as db:
            changed = db.execute("UPDATE runs SET status=?,report=?,finished=? WHERE id=? AND status IN ('running','interrupted')",
                                 (status, report[:16000], self._now(), job_id))
            if changed.rowcount != 1:
                raise ValueError("Execution claim is missing or already terminal")

    def interrupt(self, job_id: str, reason: str) -> None:
        """Record uncertainty without releasing ownership of the workspace."""
        with self._connect() as db:
            db.execute("UPDATE runs SET status='interrupted',report=? WHERE id=? AND status='running'",
                       (reason[:16000], job_id))

    def run_status(self, job_id: str) -> dict:
        with self._connect() as db:
            row = db.execute("SELECT r.*,p.path FROM runs r JOIN projects p ON p.id=r.project_id "
                             "WHERE r.id=?", (job_id,)).fetchone()
            if row is None:
                raise ValueError("Unknown execution ID")
            return dict(row)

    def discover(self, *, max_entries: int = 2000, max_depth: int = 4) -> dict:
        """Bounded discovery; never follow directory links or traverse repository internals."""
        pending = [(self.root, 0)]
        scanned = found = 0
        truncated = False
        ignored = {".git", ".forge", "node_modules", ".venv", "venv", "__pycache__", "build", "dist"}
        while pending and scanned < max_entries:
            directory, depth = pending.pop()
            if directory != self.root and ((directory / ".git").exists() or (directory / ".forge").is_dir()):
                self.register(directory)
                found += 1
                continue
            if depth >= max_depth:
                truncated = True
                continue
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        scanned += 1
                        if scanned >= max_entries:
                            truncated = True
                            break
                        path = Path(entry.path)
                        if (entry.name not in ignored and entry.is_dir(follow_symlinks=False)
                                and not path.is_symlink() and path.resolve().is_relative_to(self.root)):
                            pending.append((path, depth + 1))
            except OSError:
                truncated = True
        return {"found": found, "scanned": scanned, "truncated": truncated or bool(pending)}


def main() -> None:
    """Operator recovery entry point; deliberately absent from the agent toolset."""
    import argparse
    parser = argparse.ArgumentParser(description="Inspect or reconcile a Forge execution claim")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--reconcile", action="store_true")
    parser.add_argument("--cell-stopped", action="store_true",
                        help="Confirm you verified the old Cell/process tree has stopped")
    parser.add_argument("--note", default="", help="Evidence of shutdown and inspected partial work")
    args = parser.parse_args()
    store = Workshop(args.root, readonly=not args.reconcile)
    if args.reconcile:
        if not args.cell_stopped or not args.note.strip():
            parser.error("Reconciliation requires --cell-stopped and an evidence --note; never guess")
        store.finish(args.job, "failed", "Operator reconciliation: " + args.note)
    import sys
    sys.stdout.write(json.dumps(store.run_status(args.job), ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
