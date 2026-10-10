from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace

import pytest

from forge.model.scripted import ScriptedModel
from forge.config import ForgeSettings
from forge.runtime import ExecutionSpec, execute
from forge.workshop import Workshop


def test_identity_free_runtime_completes_without_persona_services(tmp_path):
    events = []
    workspace = tmp_path / "project"
    workspace.mkdir()

    async def emit(event):
        events.append(event)

    result = asyncio.run(execute(
        ExecutionSpec(
            job_id="job-1", role="reviewer", task="Review this empty workspace",
            workspace=workspace, model_ref="scripted",
        ),
        model=ScriptedModel([lambda _messages: ("No findings.", [])]),
        emit=emit,
        settings=ForgeSettings(workspace_root=tmp_path / "registry", cell_backend="subprocess"),
    ))

    assert result.status == "succeeded"
    assert result.report == "No findings."
    assert events[0].type == "started"
    assert events[-1].type == "done"


def test_runtime_rejects_missing_workspace(tmp_path):
    async def emit(_event):
        return None

    try:
        asyncio.run(execute(
            ExecutionSpec(
                job_id="job-2", role="coder", task="x",
                workspace=tmp_path / "missing", model_ref="scripted",
            ),
            model=ScriptedModel([]), emit=emit,
        ))
    except ValueError as exc:
        assert "workspace" in str(exc)
    else:
        raise AssertionError("missing workspace was accepted")


async def test_staging_and_cleanup_hold_the_claim_and_refusal_does_not_stage(tmp_path, monkeypatch):
    from forge.runtime import ExecutionResult
    workspace = tmp_path / "project"
    workspace.mkdir()
    settings = ForgeSettings(workspace_root=tmp_path, cell_backend="subprocess")
    store = Workshop(tmp_path)
    phases = []

    @asynccontextmanager
    async def setup():
        assert store.run_status("staged")["status"] == "running"
        phases.append("stage")
        yield
        assert store.run_status("staged")["status"] == "running"
        phases.append("cleanup")

    async def claimed(spec, **kwargs):
        phases.append("execute")
        return ExecutionResult(spec.job_id, "succeeded", "done")

    async def emit(event):
        pass

    monkeypatch.setattr("forge.runtime._execute_claimed", claimed)
    spec = ExecutionSpec(job_id="staged", role="coder", task="x", workspace=workspace, model_ref="scripted")
    await execute(spec, model=ScriptedModel([]), emit=emit, settings=settings, workspace_setup=setup)
    assert phases == ["stage", "execute", "cleanup"]
    assert store.run_status("staged")["status"] == "succeeded"
    store.claim(workspace, "another", "active")
    with pytest.raises(ValueError, match="claimed"):
        await execute(replace(spec, job_id="refused"), model=ScriptedModel([]), emit=emit, settings=settings, workspace_setup=setup)
    assert phases == ["stage", "execute", "cleanup"]
