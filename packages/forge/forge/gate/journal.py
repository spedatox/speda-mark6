"""Durable, workspace-local activity records for long-running coding work.

The chat transcript explains a conversation, while this journal explains the
repository's *work*: which job touched it, what the worker did, and the last
terminal state.  It deliberately lives under ``.forge/`` in the workspace, so
projects never share a timeline and the record survives a peer reconnect,
container replacement, or a month between coding sessions.

The journal is observational.  A full disk or an unreadable workspace must not
be allowed to stop a coding job; :class:`EventFan` contains sink failures.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forge.gate.protocol import JobEvent


class WorkspaceJournal:
    """Append ordered job events and maintain one small, restart-safe handoff.

    Daily JSONL files are an auditable action trail.  ``latest.json`` is the
    compact handoff a later job can inspect before resuming, and is written by
    replace so a crash cannot leave a half-formed state file.
    """

    def __init__(self, workspace: Path, *, task: str) -> None:
        self.workspace = workspace
        self.task = task
        self.directory = workspace / ".forge" / "activity"
        # Capture before the current job writes its initial `started` event.
        # That makes the preceding job's last-known state available to the
        # prompt even though this run immediately becomes the latest one.
        self.previous_handoff = self._load_previous()

    def _load_previous(self) -> dict[str, Any] | None:
        try:
            raw = (self.directory / "latest.json").read_text(encoding="utf-8")
            value = json.loads(raw)
            return value if isinstance(value, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    def resume_fragment(self) -> str:
        """A bounded factual handoff for the next coding job's prompt."""
        if not self.previous_handoff:
            return ""
        state = dict(self.previous_handoff)
        # A tool result can be huge; it belongs in the JSONL audit trail, not
        # in the next context window.  The final state remains inspectable.
        for key in ("last_data", "task"):
            if isinstance(state.get(key), str):
                state[key] = state[key][:4_000]
        return json.dumps(state, ensure_ascii=False, indent=2, default=str)

    async def __call__(self, event: JobEvent) -> None:
        # File I/O is tiny, but keeping it off the event loop prevents a slow
        # network-mounted workspace from delaying the live progress stream.
        import asyncio
        await asyncio.to_thread(self._record, event)

    def _record(self, event: JobEvent) -> None:
        now = datetime.now(timezone.utc)
        self.directory.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": now.isoformat(),
            "job_id": event.job_id,
            "event": event.type,
            "data": event.data,
        }
        with (self.directory / f"{now:%Y-%m-%d}.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

        # Keep a concise, durable answer to “where did Optimus leave off?”
        # on every event, including an abrupt error emitted by run_job.  Git
        # inspection is deliberately limited to lifecycle boundaries: streaming
        # a long command may produce hundreds of chunks and must stay cheap.
        state: dict[str, Any] = {
            "updated": now.isoformat(),
            "job_id": event.job_id,
            "task": self.task,
            "last_event": event.type,
            "last_data": event.data,
            "git": _git_state(self.workspace)
            if event.type in {"started", "done", "error"} else None,
        }
        target = self.directory / "latest.json"
        # Several independently-dispatched jobs can legitimately touch one
        # project. Give each atomic replace its own temporary name so they
        # never race by writing/removing the same ``latest.json.tmp``.
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=self.directory,
            prefix=".latest-", suffix=".tmp", delete=False,
        ) as handle:
            handle.write(json.dumps(state, indent=2, ensure_ascii=False, default=str))
            temporary = Path(handle.name)
        temporary.replace(target)


def _git_state(workspace: Path) -> dict[str, str] | None:
    """Return a cheap snapshot without making non-Git projects an error."""
    try:
        def run(*args: str) -> str:
            return subprocess.run(
                ["git", *args], cwd=workspace, text=True, capture_output=True,
                check=True, timeout=5,
            ).stdout.strip()
        return {
            "head": run("rev-parse", "HEAD"),
            "branch": run("branch", "--show-current"),
            "status": run("status", "--short"),
        }
    except (OSError, subprocess.SubprocessError):
        return None
