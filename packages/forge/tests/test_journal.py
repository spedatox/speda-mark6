import asyncio
import json

from forge.gate.journal import WorkspaceJournal
from forge.gate.protocol import JobEvent
from forge.config import ForgeSettings


def test_workspace_journal_records_daily_events_and_latest_handoff(tmp_path):
    journal = WorkspaceJournal(tmp_path, task="make the resume path durable")

    asyncio.run(journal(JobEvent(job_id="job-1", type="started", data={"agent": "optimus"})))
    asyncio.run(journal(JobEvent(job_id="job-1", type="done", data="checks passed")))

    activity = tmp_path / ".forge" / "activity"
    rows = [json.loads(line) for line in next(activity.glob("*.jsonl")).read_text(encoding="utf-8").splitlines()]
    assert [row["event"] for row in rows] == ["started", "done"]
    state = json.loads((activity / "latest.json").read_text(encoding="utf-8"))
    assert state["job_id"] == "job-1"
    assert state["task"] == "make the resume path durable"
    assert state["last_event"] == "done"
    assert state["last_data"] == "checks passed"


def test_workspace_journal_captures_previous_handoff_before_new_run(tmp_path):
    first = WorkspaceJournal(tmp_path, task="first task")
    asyncio.run(first(JobEvent(job_id="first", type="done", data="first result")))

    second = WorkspaceJournal(tmp_path, task="second task")

    assert "first task" in second.resume_fragment()
    assert "first result" in second.resume_fragment()


def test_default_agent_workspace_is_a_durable_workspace(tmp_path):
    """The server's no-cwd jobs still have a real workspace to journal."""
    settings = ForgeSettings(workspace_root=tmp_path)
    assert (settings.workspace_root / "optimus") == tmp_path / "optimus"
