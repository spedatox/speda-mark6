import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest

from forge.cell.base import CellCleanupError, CellPolicy
from forge.cell.docker_cell import DockerCell
from forge.config import ForgeSettings
from forge.model.scripted import ScriptedModel, tool_call
from forge.runtime import ExecutionSpec, execute
from forge.workshop import Workshop


def test_checkpoint_survives_restart_and_rejects_stale_revision(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = Workshop(tmp_path)
    state = {"objective": "Ship the editor", "status": "active", "next_steps": ["Test undo"]}
    project = store.checkpoint(workspace, revision=0, state=state)
    reopened = Workshop(tmp_path, readonly=True)
    assert reopened.inspect(project["id"])["checkpoint"] == state
    with pytest.raises(ValueError, match="changed"):
        store.checkpoint(workspace, revision=0, state={**state, "progress": "stale"})
    assert reopened.inspect(project["id"])["revision"] == 1


def test_claim_is_atomic_across_independent_connections(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    stores = [Workshop(tmp_path), Workshop(tmp_path)]

    def claim(i):
        try:
            stores[i].claim(workspace, f"run-{i}", "edit")
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(claim, range(2))) == [False, True]


def test_restart_does_not_steal_claim_and_parent_paths_conflict(tmp_path):
    workspace = tmp_path / "project"
    child = workspace / "nested"
    child.mkdir(parents=True)
    store = Workshop(tmp_path)
    store.claim(child, "original", "edit")
    reopened = Workshop(tmp_path)
    with pytest.raises(ValueError, match="claimed"):
        reopened.claim(workspace, "replacement", "edit parent")
    reopened.finish("original", "failed", "operator reconciled the old Cell")
    reopened.claim(workspace, "replacement", "continue verified files")
    with pytest.raises(ValueError, match="claimed"):
        reopened.claim(child, "child", "edit child")


def test_finished_job_is_not_replayed_and_other_projects_can_run(tmp_path):
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()
    store = Workshop(tmp_path)
    project = store.claim(first, "first", "task")
    store.claim(second, "second", "other task")
    store.finish("first", "succeeded", "tests passed")
    with pytest.raises(ValueError, match="already exists"):
        store.claim(first, "first", "different task")
    assert store.inspect(project["id"])["runs"][0]["report"] == "tests passed"
    store.claim(first, "third", "next milestone")


def test_discovery_is_bounded_skips_dependencies_and_reports_missing_paths(tmp_path):
    repo = tmp_path / "team" / "editor"
    (repo / ".git").mkdir(parents=True)
    (tmp_path / "node_modules" / "dependency" / ".git").mkdir(parents=True)
    store = Workshop(tmp_path)
    assert store.discover()["found"] == 1
    inventory = store.list_projects()
    assert inventory["total"] == 1
    repo.rename(repo.with_name("moved"))
    assert store.list_projects()["projects"][0]["available"] is False
    assert store.discover(max_entries=1)["truncated"] is True


def test_readonly_status_does_not_create_storage(tmp_path):
    with pytest.raises(FileNotFoundError):
        Workshop(tmp_path, readonly=True)
    assert not (tmp_path / ".forge").exists()


def test_worker_cannot_mount_the_execution_database(tmp_path):
    store = Workshop(tmp_path)
    with pytest.raises(ValueError, match="individual project"):
        store.claim(tmp_path, "root-job", "edit everything")
    with pytest.raises(ValueError, match="individual project"):
        store.claim(tmp_path.parent, "parent-job", "edit everything")


def test_invalid_completion_does_not_overwrite_checkpoint(tmp_path):
    store = Workshop(tmp_path)
    with pytest.raises(ValueError, match="outstanding"):
        store.checkpoint(tmp_path, revision=0, state={
            "objective": "ship", "status": "complete", "blockers": ["tests failing"],
        })


def test_runtime_injects_project_handoff_and_keeps_claim_on_uncertain_cleanup(tmp_path, monkeypatch):
    import forge.runtime as runtime
    from forge.warden.state import Terminal, StopReason
    root = tmp_path / "workshop"
    workspace = root / "project"
    workspace.mkdir(parents=True)
    store = Workshop(root)
    project = store.checkpoint(workspace, revision=0, state={
        "objective": "Implement undo", "status": "active", "next_steps": ["Test undo after reload"],
    })
    captured = []

    async def run_job(*_args, **kwargs):
        captured.extend(kwargs["fragments"])
        return Terminal(reason=StopReason.COMPLETED, final_text="undo verified")

    async def emit(_event):
        pass

    monkeypatch.setattr(runtime, "run_job", run_job)
    spec = ExecutionSpec(job_id="first", role="coder", task="continue", workspace=workspace, model_ref="test")
    settings = ForgeSettings(workspace_root=root)
    result = asyncio.run(execute(spec, model=ScriptedModel([]), emit=emit, settings=settings))
    assert result.status == "succeeded"
    assert "Test undo after reload" in str(captured)
    assert store.inspect(project["id"])["checkpoint"]["status"] == "active"

    async def uncertain(*_args, **_kwargs):
        raise CellCleanupError("daemon unavailable")

    monkeypatch.setattr(runtime, "run_job", uncertain)
    from dataclasses import replace
    with pytest.raises(CellCleanupError):
        asyncio.run(execute(replace(spec, job_id="second"), model=ScriptedModel([]), emit=emit, settings=settings))
    assert store.inspect(project["id"])["active_runs"][0]["id"] == "second"
    assert store.run_status("second")["status"] == "interrupted"
    with pytest.raises(ValueError, match="claimed"):
        asyncio.run(execute(replace(spec, job_id="third"), model=ScriptedModel([]), emit=emit, settings=settings))


def test_docker_failed_removal_is_not_reported_as_shutdown(monkeypatch):
    cell = DockerCell("test", "test-image", CellPolicy())

    async def failed(*_args, **_kwargs):
        return 1, b"", b"daemon unavailable"

    monkeypatch.setattr(cell, "_docker", failed)
    with pytest.raises(CellCleanupError):
        asyncio.run(cell.close())


def test_factory_cleans_up_partially_started_cell(tmp_path, monkeypatch):
    import forge.cell.factory as factory
    closed = []

    class BrokenCell:
        def __init__(self, **kwargs):
            pass

        async def start(self):
            raise RuntimeError("startup failed after creation")

        async def close(self):
            closed.append(True)

    monkeypatch.setitem(factory._BACKENDS, "subprocess", BrokenCell)
    with pytest.raises(RuntimeError, match="startup failed"):
        asyncio.run(factory.build_cell(agent_id="test", workspace_root=tmp_path, backend="subprocess"))
    assert closed == [True]

    async def cannot_close(self):
        raise RuntimeError("daemon gone")

    monkeypatch.setattr(BrokenCell, "close", cannot_close)
    with pytest.raises(CellCleanupError):
        asyncio.run(factory.build_cell(agent_id="test", workspace_root=tmp_path, backend="subprocess"))


def test_two_fresh_workers_continue_real_project_files(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    settings = ForgeSettings(workspace_root=tmp_path, cell_backend="subprocess")
    store = Workshop(tmp_path)
    project = store.checkpoint(workspace, revision=0, state={
        "objective": "Increment state across milestones", "status": "active",
        "next_steps": ["Create initial state", "Increment it"],
    })
    events = []
    prompts = []

    class RecordingModel(ScriptedModel):
        async def stream(self, **kwargs):
            prompts.append(kwargs["system"])
            async for event in super().stream(**kwargs):
                yield event

    async def emit(event):
        events.append(event)

    def run(job_id, steps):
        return asyncio.run(execute(
            ExecutionSpec(job_id=job_id, role="coder", task="next milestone",
                          workspace=workspace, model_ref="scripted"),
            model=RecordingModel(steps), emit=emit, settings=settings,
        ))

    first = run("first", [
        lambda _: ("create state", [tool_call("write_file", path="state.py", content="value = 1\n")]),
        lambda _: ("check", [tool_call("run_command", command='python -B -c "from state import value; assert value == 1"')]),
        lambda _: ("Initial state verified", []),
    ])
    assert first.status == "succeeded"
    store.checkpoint(workspace, revision=project["revision"], state={
        "objective": "Increment state across milestones", "status": "active",
        "progress": "Initial state verified", "next_steps": ["Increment existing value to two"],
    })
    second = run("second", [
        lambda _: ("read existing state", [tool_call("read_file", path="state.py")]),
        lambda _: ("increment", [tool_call("edit_file", path="state.py", old_string="value = 1", new_string="value = 2")]),
        lambda _: ("verify", [tool_call("run_command", command='python -B -c "from state import value; assert value == 2"')]),
        lambda _: ("Increment verified", []),
    ])
    assert second.status == "succeeded"
    assert (workspace / "state.py").read_text() == "value = 2\n"
    assert "Initial state verified" in prompts[-1]
    assert "Increment existing value to two" in prompts[-1]
    assert not [event for event in events if event.type == "tool_result" and event.data.get("is_error")]
    assert not store.inspect(project["id"])["active_runs"]
