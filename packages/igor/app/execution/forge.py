# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""In-process adapter from Igor's model client to Forge's anonymous runtime."""
from __future__ import annotations

import asyncio
import base64
import binascii
import shutil
import sys
from pathlib import Path, PurePosixPath
from typing import AsyncIterator, Callable

from app.config import settings
def _load_runtime():
    forge_dir = Path(settings.forge_dir).expanduser().resolve() if settings.forge_dir else None
    if forge_dir is None or not (forge_dir / "forge" / "runtime" / "__init__.py").is_file():
        raise RuntimeError(
            "Forge execution is unavailable: set FORGE_DIR to the Forge repository."
        )
    value = str(forge_dir)
    if value not in sys.path:
        sys.path.insert(0, value)
    from forge.runtime import ExecutionSpec, execute
    return ExecutionSpec, execute


def _resolve_workspace(value: str) -> Path:
    """Map a Hisar picker path and enforce the configured execution boundary."""
    raw = Path(value).expanduser()
    if not settings.forge_workspace_root:
        return raw.resolve()

    root = Path(settings.forge_workspace_root).expanduser().resolve()
    wire = PurePosixPath(value)
    prefix = ("/", "Forge", "workspaces")
    if wire.parts[:3] == prefix:
        candidate = root.joinpath(*wire.parts[3:])
    else:
        candidate = raw

    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Forge workspace is outside the allowed root: {value}") from exc
    return resolved


class IgorModelAdapter:
    """Forge Model protocol backed by Mark VI's single LLMClient."""

    def __init__(self, client, model_ref: str) -> None:
        self._client = client
        self.model_id = model_ref

    async def stream(
        self, *, system: str, messages: list[dict], tools: list[dict],
        signal: asyncio.Event,
    ) -> AsyncIterator[object]:
        if signal.is_set():
            return
        from forge.model.base import TextDelta, ToolUseRequest, TurnEnd, UsageReport

        response = await self._client.create_message(
            model=self.model_id,
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=settings.chat_max_output_tokens,
        )
        for block in response.content:
            if signal.is_set():
                return
            if block.type == "text":
                yield TextDelta(block.text)
            elif block.type == "tool_use":
                yield ToolUseRequest(
                    id=block.id,
                    name=block.name,
                    input=block.input,
                    reasoning_content=getattr(block, "reasoning_content", None),
                )
        usage = getattr(response, "usage", None)
        if usage is not None:
            yield UsageReport(
                input_tokens=getattr(usage, "input_tokens", 0) or 0,
                output_tokens=getattr(usage, "output_tokens", 0) or 0,
                cache_read=getattr(usage, "cache_read_input_tokens", 0) or 0,
                cache_write=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            )
        yield TurnEnd(reason=getattr(response, "stop_reason", None))


class ForgeExecutor:
    def __init__(self, client) -> None:
        self._client = client

    async def run(
        self, *, job_id: str, role: str, task: str, workspace: str,
        model_ref: str, emit: Callable[[dict], None], inputs: list[dict] | None = None,
    ) -> str:
        ExecutionSpec, execute = _load_runtime()

        workspace_path = _resolve_workspace(workspace)
        safe_job = "".join(c for c in job_id if c.isalnum() or c in "-_")[:96] or "job"
        input_root = (workspace_path / ".forge" / "inputs").resolve()
        try:
            input_root.relative_to(workspace_path)
        except ValueError as exc:
            raise ValueError("Forge input directory escapes the selected workspace") from exc
        input_dir = input_root / safe_job
        materialized: list[str] = []
        if inputs:
            input_dir.mkdir(parents=True, exist_ok=False)
            try:
                for index, item in enumerate(inputs, 1):
                    name = Path(str(item.get("name") or f"input-{index}")).name
                    if name in {"", ".", ".."}:
                        name = f"input-{index}"
                    destination = input_dir / name
                    if destination.exists():
                        destination = input_dir / f"{index}-{name}"
                    data = base64.b64decode(str(item.get("data") or ""), validate=True)
                    destination.write_bytes(data)
                    materialized.append(str(destination.relative_to(workspace_path)))
            except (ValueError, binascii.Error, OSError) as exc:
                shutil.rmtree(input_dir, ignore_errors=True)
                raise ValueError(f"could not materialize uploaded file {name}") from exc

        if materialized:
            task = (
                f"{task}\n\nMark VI materialized the original uploaded files at these "
                f"workspace-relative paths:\n- " + "\n- ".join(materialized)
            )

        async def forward(event) -> None:
            data = event.data
            if event.type == "chunk" and data:
                emit({"phase": "text", "text": str(data), "source": "forge"})
            elif event.type == "tool" and isinstance(data, dict):
                emit({
                    "phase": "tool", "tool": data.get("name"),
                    "tool_call_id": data.get("id"), "input": data.get("input"),
                    "source": "forge",
                })
            elif event.type == "tool_result" and isinstance(data, dict):
                content = data.get("content", data.get("result", ""))
                emit({
                    "phase": "tool_result",
                    "tool_call_id": data.get("tool_use_id", data.get("id")),
                    "result": str(content)[:1500], "source": "forge",
                })

        images = {
            "coder": settings.forge_coder_image,
            "reviewer": settings.forge_reviewer_image,
            "pentester": settings.forge_pentester_image,
        }

        try:
            result = await execute(
                ExecutionSpec(
                    job_id=job_id,
                    role=role,
                    task=task,
                    workspace=workspace_path,
                    model_ref=model_ref,
                    max_iterations=settings.forge_worker_max_iterations,
                    timeout_s=settings.forge_worker_timeout_s,
                    cell_backend=settings.forge_cell_backend,
                    cell_image=images[role],
                ),
                model=IgorModelAdapter(self._client, model_ref),
                emit=forward,
            )
        finally:
            if inputs and input_dir.is_dir():
                shutil.rmtree(input_dir)
        if result.status != "succeeded":
            raise RuntimeError(result.error or result.report or f"Forge job {result.status}")
        return result.report or "(Forge completed without a text report)"
