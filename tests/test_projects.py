import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
import pytest
from sqlalchemy import select, update
from app.config import Settings
from app.models.coordination import ProjectTask, CoordinationRun
from app.models.model_policy import ToolExecutionReceipt
from app.schemas.coordination import CreateProject, CreateRoom, Checkpoint, RoomInput
from app.schemas.llm import LLMMessage, Block
from app.services.coordination_budget import CoordinationError
from app.services.projects import Projects, snapshot, inventory
from app.skills.collaboration import ProjectStatusSkill, ProjectDelegateSkill
from forge.model.base import ToolUseRequest
from forge.runtime import ExecutionResult
from forge.warden.tool import ToolResult
from tests.test_coordination import setup, send, reply


async def test_stopping_reply_preserves_delegated_work_and_unknown_charge(memory, tmp_path):
    executed = []
    async def executor(spec, **kwargs):
        executed.append(spec.job_id)
        return ExecutionResult(spec.job_id, "succeeded", "Independent work completed")
    bits = await project_setup(memory, tmp_path, [delegate()], executor)
    original = bits.client.stream_message
    entered, closed = asyncio.Event(), asyncio.Event()
    class SlowStream:
        async def event_stream(self):
            entered.set()
            yield "text", "Still speaking"
            await asyncio.Event().wait()
        async def get_final_message(self):
            raise AssertionError("Cancelled response")
    @asynccontextmanager
    async def stream(**args):
        if not bits.client.calls:
            async with original(**args) as result:
                yield result
        else:
            try:
                yield SlowStream()
            finally:
                closed.set()
    bits.client.stream_message = stream
    room = await bits.coordinator.create(1, CreateRoom(project_id=bits.project["id"]))
    admitted = await bits.coordinator.start(1, room["id"], RoomInput(input_id="one", message="Delegate this"))
    task = bits.coordinator.live[admitted["run_id"]]
    await asyncio.wait_for(entered.wait(), 5)
    run = await bits.coordinator.inspect_run(1, admitted["run_id"])
    await bits.coordinator.stop_reply(1, run["steps"][0]["id"])
    await asyncio.wait_for(task, 10)
    run = await bits.coordinator.inspect_run(1, admitted["run_id"])
    assert closed.is_set() and len(executed) == 1
    assert not run["cancelled"] and run["status"] == "reconciling"
    assert sum(c["status"] == "unknown" for c in run["calls"]) == 1
    state = await bits.projects.inspect(1, bits.project["id"])
    assert state["tasks"][0]["status"] == "succeeded"
    events = (await bits.coordinator.events(1, room["id"]))["events"]
    assert any(e["kind"] == "delta" and e["payload"]["text"] == "Still speaking" for e in events)
    await bits.coordinator.shutdown()


def delegate(role="autobot", dependencies=()):
    return LLMMessage([Block(type="tool_use", id="delegate1", name="project_delegate", input={
        "role": role, "task": "Update the example", "acceptance": ["Keep the canonical project intact"],
        "dependencies": list(dependencies)})], "tool_use")


async def project_setup(memory, tmp_path, script, executor):
    bits = await setup(memory, script)
    root = tmp_path / "projects"
    path = root / "example"
    path.mkdir(parents=True)
    (path / "example.txt").write_text("original")
    config = Settings(_env_file=None, project_workspace_root=str(root),
        project_state_root=str(tmp_path / "state"), project_host_id="test-hisar", project_execution_enabled=True,
        project_supervision_enabled=False)  # These fixtures assert historical call order; live supervision has its own suite.
    projects = Projects(memory.sessions, bits.coordinator, config, executor=executor)
    bits.coordinator.projects = projects
    await bits.engine._registry.register_skill(ProjectStatusSkill())
    await bits.engine._registry.register_skill(ProjectDelegateSkill())
    project = await projects.create(1, CreateProject(name="Example", path=str(path)))
    bits.projects, bits.project, bits.path = projects, project, path
    return bits


async def test_isolated_execution_shared_budget_checkpoint_and_restart(memory, tmp_path):
    observed = {}
    async def executor(spec, *, model, emit, signal, settings, effect_gate):
        observed["spec"] = spec
        assert spec.role == "coder" and not spec.allow_network and settings.retry_attempts == 0
        assert (spec.workspace / "example.txt").read_text() == "original"
        chunks = [e async for e in model.stream(system="Anonymous coding worker", messages=[{
            "role": "user", "content": spec.task}], tools=[], signal=signal)]
        async def write():
            (spec.workspace / "example.txt").write_text("updated")
            return ToolResult("File updated")
        result = await effect_gate(ToolUseRequest("write1", "write_file", {"path": "example.txt"}), write)
        assert result.content == "File updated"
        return ExecutionResult(spec.job_id, "succeeded", "Changed example.txt; integration remains pending")
    bits = await project_setup(memory, tmp_path, [delegate(), reply("The worker is queued."), reply("Proceeding")], executor)
    await bits.projects.checkpoint(1, bits.project["id"], Checkpoint(expected_revision=0,
        objective="Improve the example", acceptance=["Keep original intact"], next_steps=["Implement"] ))
    room = await bits.coordinator.create(1, CreateRoom(lead="optimus", project_id=bits.project["id"]))
    result = await send(bits, room)
    assert result["status"] == "succeeded", await bits.coordinator.events(1, room["id"])
    assert result["reserved"]["calls"] == 3
    assert bits.path.joinpath("example.txt").read_text() == "original"
    state = await bits.projects.inspect(1, bits.project["id"])
    task = state["tasks"][0]
    assert task["result"]["integration"] == "pending"
    assert Path(task["result"]["workspace"]).joinpath("example.txt").read_text() == "updated"
    assert state["workshop"]["checkpoint"]["status"] == "active"
    assert state["workshop"]["revision"] == 2 and not state["workshop"]["active_runs"]
    restarted = Projects(memory.sessions, bits.coordinator, bits.projects.settings, executor=executor)
    assert (await restarted.inspect(1, bits.project["id"]))["tasks"][0]["result"] == task["result"]
    assert (await restarted.create(1, CreateProject(name="Again", path=str(bits.path))))["id"] == bits.project["id"]
    with pytest.raises(CoordinationError, match="changed"):
        await restarted.checkpoint(1, bits.project["id"], Checkpoint(expected_revision=0, objective="Stale"))
    with pytest.raises(CoordinationError):
        await restarted.inspect(2, bits.project["id"])
    await bits.coordinator.shutdown()


async def test_uncertain_cell_holds_claim_and_blocks_replacement(memory, tmp_path):
    async def executor(spec, **kwargs):
        raise RuntimeError("Cell teardown could not be confirmed")
    bits = await project_setup(memory, tmp_path, [delegate(), reply("Queued")], executor)
    room = await bits.coordinator.create(1, CreateRoom(project_id=bits.project["id"]))
    result = await send(bits, room)
    assert result["status"] == "reconciling"
    state = await bits.projects.inspect(1, bits.project["id"])
    assert state["tasks"][0]["status"] == "reconciling"
    assert state["workshop"]["active_runs"][0]["status"] == "interrupted"
    with pytest.raises(ValueError, match="claimed"):
        await asyncio.to_thread(bits.projects.workshop.claim, bits.path, "replacement", "retry")
    await bits.coordinator.shutdown()


async def test_worker_effect_journal_failure_prevents_effect(memory, tmp_path):
    executed = False
    async def executor(spec, *, effect_gate, **kwargs):
        nonlocal executed
        async with memory.sessions() as db, db.begin():
            db.add(ToolExecutionReceipt(user_id=1, request_id=spec.job_id, call_id="uncertain",
                name="write_file", arguments_hash="old", status="started"))
        async def write():
            nonlocal executed
            executed = True
            return ToolResult("bad")
        return await effect_gate(ToolUseRequest("uncertain", "write_file", {}), write)
    bits = await project_setup(memory, tmp_path, [delegate(), reply("Queued")], executor)
    room = await bits.coordinator.create(1, CreateRoom(project_id=bits.project["id"]))
    result = await send(bits, room)
    assert not executed and result["status"] == "reconciling"
    await bits.coordinator.shutdown()


def test_snapshot_preserves_source_ignores_credentials_and_detects_changes(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "file.txt").write_text("hello")
    (source / ".env").write_text("fake-secret")
    (source / ".env.example").write_text("template")
    target = tmp_path / "copy"
    revision = snapshot(source, target)
    assert revision == inventory(target)[0]
    assert not (target / ".env").exists() and (target / ".env.example").exists()
    (target / "file.txt").write_text("modified")
    assert inventory(target)[0] != revision
    assert (source / "file.txt").read_text() == "hello"


async def test_actual_forge_loop_uses_adapted_provider_and_receipts(memory, tmp_path, monkeypatch):
    """Real Warden/Gate/Workshop; only the provider and container boundary are fixtures."""
    from forge.runtime import execute
    from forge.cell.base import Cell, CommandResult
    import forge.gate.runner as runner
    cells = []
    class FixtureCell(Cell):
        def __init__(self, workspace, policy):
            self.workspace, self.policy, self.closed = workspace, policy, False
        async def start(self): pass
        async def run(self, command, **kwargs):
            return CommandResult("", "Command execution unavailable in fixture", 1)
        async def read(self, path): return (self.workspace / path).read_text()
        async def write(self, path, content): (self.workspace / path).write_text(content)
        async def reset(self): raise AssertionError("Unexpected reset")
        async def close(self): self.closed = True
    async def build(**kwargs):
        cell = FixtureCell(kwargs["workspace"], kwargs["policy"])
        cells.append(cell)
        return cell
    async def no_graph(self): return False
    monkeypatch.setattr(runner, "build_cell", build)
    monkeypatch.setattr(runner.GraphSidecar, "start", no_graph)
    read = LLMMessage([Block(type="tool_use", id="read-evidence", name="read_file",
        input={"path": "example.txt"})], "tool_use")
    bits = await project_setup(memory, tmp_path, [delegate("reviewer"), reply("Review queued"),
        read, reply("example.txt contains original. No changes made.")], execute)
    # Carry Kimi's opaque native continuation through Warden's portable history.
    # The provider codec must reconstruct the exact previous assistant message.
    from app.services.llm_protocol import decode, request_body
    policy = await bits.policy.resolve(user_id=1)
    native = {"role": "assistant", "content": "", "reasoning_content": "fixture native continuation",
        "tool_calls": [{"id": "read-evidence", "type": "function", "function": {
            "name": "read_file", "arguments": '{"path":"example.txt"}'}}]}
    bits.client.script[2] = decode(policy, {"choices": [{"finish_reason": "tool_calls", "message": native}]})
    room = await bits.coordinator.create(1, CreateRoom(project_id=bits.project["id"]))
    result = await send(bits, room)
    state = await bits.projects.inspect(1, bits.project["id"])
    assert result["status"] == "succeeded", state
    assert len(cells) == 1 and cells[0].closed
    assert state["cells"][0]["status"] == "closed"
    assert len(bits.client.calls) == 4
    assert "original" in str(bits.client.calls[-1]["messages"])
    last = bits.client.calls[-1]
    wire = request_body(last["policy"], last["system"], last["messages"], last["tools"], last["max_tokens"])
    assert native in wire["messages"]
    events = (await bits.coordinator.events(1, room["id"]))["events"]
    assert "fixture native continuation" not in str(events)
    assert not ({"write_file", "edit_file", "run_command", "task"} & {t["name"] for t in bits.client.calls[-1]["tools"]})
    async with memory.sessions() as db:
        receipt = await db.scalar(select(ToolExecutionReceipt).where(ToolExecutionReceipt.call_id == "read-evidence"))
        assert receipt.status == "completed" and "original" in receipt.result["content"]
    await bits.coordinator.shutdown()


async def test_dependent_review_uses_exact_worker_artifact(memory, tmp_path):
    from app.schemas.coordination import Delegate
    from app.core.context import AgentContext
    from app.models.coordination import AgentStep, Room
    seen = []
    async def executor(spec, **kwargs):
        seen.append((spec.role, (spec.workspace / "example.txt").read_text()))
        if spec.role == "coder":
            (spec.workspace / "example.txt").write_text("worker revision")
        return ExecutionResult(spec.job_id, "succeeded", "Inspected this revision")
    bits = await project_setup(memory, tmp_path, [], executor)
    room = await bits.coordinator.create(1, CreateRoom(project_id=bits.project["id"]))
    bits.coordinator.launch = lambda _: None
    admitted = await bits.coordinator.start(1, room["id"], RoomInput(input_id="one", message="Implement then review"))
    import time
    async with memory.sessions() as db, db.begin():
        run = await db.get(CoordinationRun, admitted["run_id"])
        run.status, run.executor, run.lease_until = "running", bits.coordinator.identity, time.time()+60
        step = await db.scalar(select(AgentStep).where(AgentStep.run_id == run.id))
    ctx = memory.context(run_id=run.id, room_id=room["id"], project_id=bits.project["id"], step_id=step.id,
        coordination=bits.coordinator, tool_call_id="build")
    first = await bits.projects.delegate(ctx, Delegate(role="autobot", task="Implement", acceptance=["Retain original"]))
    ctx.tool_call_id = "review"
    second = await bits.projects.delegate(ctx, Delegate(role="reviewer", task="Review exact result",
        acceptance=["Read the worker revision"], dependencies=[first["task_id"]]))
    await bits.projects.drain(run.id)
    assert seen == [("coder", "original"), ("reviewer", "worker revision")]
    state = await bits.projects.inspect(1, bits.project["id"])
    records = {r["id"]: r for r in state["tasks"]}
    assert records[second["task_id"]]["base_revision"] == records[first["task_id"]]["result"]["revision"]
    assert (bits.path / "example.txt").read_text() == "original"
    await bits.coordinator.shutdown()
