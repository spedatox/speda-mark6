# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

import base64
from types import SimpleNamespace

from app.execution.forge import ForgeExecutor


async def test_existing_upload_is_materialized_for_forge_then_removed(tmp_path, monkeypatch):
    observed = {}

    class _Spec:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    async def _execute(spec, **_kwargs):
        uploaded = spec.workspace / ".forge" / "inputs" / "job-1" / "notes.txt"
        observed["task"] = spec.task
        observed["bytes"] = uploaded.read_bytes()
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
