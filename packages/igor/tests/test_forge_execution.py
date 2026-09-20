# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

import base64
import sys
from types import SimpleNamespace

from app.execution.forge import ForgeExecutor, _load_runtime, _resolve_workspace


def test_native_runtime_imports_from_workspace_package():
    ExecutionSpec, execute = _load_runtime()

    assert ExecutionSpec.__module__ == "forge.runtime"
    assert execute.__module__ == "forge.runtime"


def test_native_runtime_ignores_legacy_checkout_env_and_does_not_mutate_path(
    monkeypatch,
):
    monkeypatch.setenv("FORGE_DIR", "/missing/legacy-forge-checkout")
    before = list(sys.path)

    ExecutionSpec, execute = _load_runtime()

    assert sys.path == before
    assert ExecutionSpec.__module__ == "forge.runtime"
    assert execute.__module__ == "forge.runtime"


def test_hisar_workspace_is_mapped_to_host_visible_root(tmp_path, monkeypatch):
    workspace = tmp_path / "optimus" / "project"
    workspace.mkdir(parents=True)
    monkeypatch.setattr(
        "app.execution.forge.settings.forge_workspace_root", str(tmp_path)
    )

    assert _resolve_workspace("/Forge/workspaces/optimus/project") == workspace.resolve()


def test_workspace_outside_configured_root_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.execution.forge.settings.forge_workspace_root", str(tmp_path / "allowed")
    )

    try:
        _resolve_workspace(str(tmp_path / "elsewhere"))
    except ValueError as exc:
        assert "outside the allowed root" in str(exc)
    else:
        raise AssertionError("workspace outside the configured root was accepted")


async def test_existing_upload_is_materialized_for_forge_then_removed(tmp_path, monkeypatch):
    observed = {}

    class _Spec:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    async def _execute(spec, **_kwargs):
        uploaded = spec.workspace / ".forge" / "inputs" / "job-1" / "notes.txt"
        observed["task"] = spec.task
        observed["bytes"] = uploaded.read_bytes()
        observed["image"] = spec.cell_image
        return SimpleNamespace(status="succeeded", report="done", error=None)

    monkeypatch.setattr("app.execution.forge._load_runtime", lambda: (_Spec, _execute))
    result = await ForgeExecutor(object()).run(
        job_id="job-1", role="coder", task="use the notes",
        workspace=str(tmp_path), model_ref="test:model", emit=lambda _event: None,
        inputs=[{
            "name": "notes.txt", "media_type": "text/plain",
            "data": base64.b64encode(b"important").decode(),
        }],
    )

    assert result == "done"
    assert observed["bytes"] == b"important"
    assert observed["image"] == "forge-cell-optimus:latest"
    assert ".forge" in observed["task"]
    assert not (tmp_path / ".forge" / "inputs" / "job-1").exists()


async def test_upload_filename_cannot_escape_workspace(tmp_path, monkeypatch):
    class _Spec:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    async def _execute(spec, **_kwargs):
        assert (spec.workspace / ".forge" / "inputs" / "job-2" / "escape.txt").is_file()
        return SimpleNamespace(status="succeeded", report="safe", error=None)

    monkeypatch.setattr("app.execution.forge._load_runtime", lambda: (_Spec, _execute))
    await ForgeExecutor(object()).run(
        job_id="job-2", role="coder", task="x", workspace=str(tmp_path),
        model_ref="test:model", emit=lambda _event: None,
        inputs=[{
            "name": "../../escape.txt", "media_type": "text/plain",
            "data": base64.b64encode(b"inside").decode(),
        }],
    )
    assert not (tmp_path.parent / "escape.txt").exists()


async def test_pentester_uses_security_cell_image(tmp_path, monkeypatch):
    observed = {}

    class _Spec:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    async def _execute(spec, **_kwargs):
        observed["image"] = spec.cell_image
        return SimpleNamespace(status="succeeded", report="checked", error=None)

    monkeypatch.setattr("app.execution.forge._load_runtime", lambda: (_Spec, _execute))
    result = await ForgeExecutor(object()).run(
        job_id="job-3", role="pentester", task="audit local code",
        workspace=str(tmp_path), model_ref="test:model", emit=lambda _event: None,
    )

    assert result == "checked"
    assert observed["image"] == "forge-cell-scourge:latest"


async def test_forge_tool_events_preserve_ids_and_tool_names(tmp_path, monkeypatch):
    events = []

    class _Spec:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    async def _execute(_spec, **kwargs):
        await kwargs["emit"](SimpleNamespace(
            type="tool", data={"id": "forge-a", "name": "shell", "input": {"cmd": "a"}},
        ))
        await kwargs["emit"](SimpleNamespace(
            type="tool", data={"id": "forge-b", "name": "read_file", "input": {"path": "b"}},
        ))
        await kwargs["emit"](SimpleNamespace(
            type="tool_result", data={"tool_use_id": "forge-b", "content": "result-b"},
        ))
        await kwargs["emit"](SimpleNamespace(
            type="tool_result", data={"tool_use_id": "forge-a", "content": "result-a"},
        ))
        return SimpleNamespace(status="succeeded", report="done", error=None)

    monkeypatch.setattr("app.execution.forge._load_runtime", lambda: (_Spec, _execute))
    await ForgeExecutor(object()).run(
        job_id="job-events", role="coder", task="x", workspace=str(tmp_path),
        model_ref="test:model", emit=events.append,
    )

    assert [(e["phase"], e["tool_call_id"], e["tool"]) for e in events] == [
        ("tool", "forge-a", "shell"),
        ("tool", "forge-b", "read_file"),
        ("tool_result", "forge-b", "read_file"),
        ("tool_result", "forge-a", "shell"),
    ]


async def test_igor_model_adapter_propagates_thought_signature_to_assistant_message():
    import asyncio
    from app.execution.forge import IgorModelAdapter
    from app.services.llm_client import LLMMessage, ToolUseBlock, _translate_message
    from forge.model.base import ToolUseRequest
    from forge.warden.engine import _Turn

    class _MockClient:
        async def create_message(self, **_kwargs):
            return LLMMessage(
                content=[
                    ToolUseBlock(
                        id="call_run_cmd",
                        name="run_command",
                        input={"cmd": "ls -la"},
                        signature="SIG_GEMINI_ROUNDTRIP_123",
                    )
                ],
                stop_reason="tool_use",
            )

    adapter = IgorModelAdapter(_MockClient(), "gemini:gemini-2.5-flash")
    events = [
        ev
        async for ev in adapter.stream(
            system="test", messages=[], tools=[], signal=asyncio.Event()
        )
    ]

    tool_uses = [ev for ev in events if isinstance(ev, ToolUseRequest)]
    assert len(tool_uses) == 1
    assert tool_uses[0].signature == "SIG_GEMINI_ROUNDTRIP_123"

    turn = _Turn(tool_uses=tool_uses)
    assistant_msg = turn.assistant_message()
    assert assistant_msg["role"] == "assistant"
    assert assistant_msg["content"][0]["type"] == "tool_use"
    assert assistant_msg["content"][0]["_signature"] == "SIG_GEMINI_ROUNDTRIP_123"

    translated = _translate_message(assistant_msg)
    assert translated[0]["role"] == "assistant"
    assert translated[0]["tool_calls"][0]["extra_content"] == {
        "google": {"thought_signature": "SIG_GEMINI_ROUNDTRIP_123"}
    }

