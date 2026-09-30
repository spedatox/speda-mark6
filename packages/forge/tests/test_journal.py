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


def test_large_tool_output_is_bounded_and_git_survives_stream_chunks(tmp_path, monkeypatch):
    monkeypatch.setattr("forge.gate.journal._git_state", lambda _workspace: {"head": "abc"})
    journal = WorkspaceJournal(tmp_path, task="continue")
    asyncio.run(journal(JobEvent(job_id="one", type="started")))
    asyncio.run(journal(JobEvent(job_id="one", type="tool_result", data={"content": "x" * 50000})))
    handoff = json.loads(WorkspaceJournal(tmp_path, task="next").resume_fragment())
    assert handoff["git"] == {"head": "abc"}
    assert "truncated_preview" in handoff["last_data"]
    assert len(json.dumps(handoff)) < 6000


def test_corrupt_handoff_is_reported_instead_of_silently_forgotten(tmp_path):
    activity = tmp_path / ".forge" / "activity"
    activity.mkdir(parents=True)
    (activity / "latest.json").write_text("{broken", encoding="utf-8")
    assert "could not be read" in WorkspaceJournal(tmp_path, task="next").resume_fragment()
