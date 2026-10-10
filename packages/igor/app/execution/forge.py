# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""In-process adapter from Igor's model client to Forge's anonymous runtime."""
from __future__ import annotations

import asyncio
import base64
import binascii
import inspect
import shutil
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath
from typing import AsyncIterator, Callable

from app.config import settings


def _load_runtime():
    from forge.runtime import ExecutionSpec, execute
    return ExecutionSpec, execute


def _resolve_workspace(value: str, *, allowed_root: str | None = None) -> Path:
    """Map a Hisar picker path and enforce the configured execution boundary."""
    raw = Path(value).expanduser()
    configured_root = settings.forge_workspace_root if allowed_root is None else allowed_root
    if not configured_root:
        return raw.resolve()

    root = Path(configured_root).expanduser().resolve()
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

        call = asyncio.create_task(self._client.create_message(
            model=self.model_id,
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=settings.chat_max_output_tokens,
        ))
        watch = asyncio.create_task(signal.wait())
        try:
            # Codex tools/parallel.rs: cancellation owns the in-flight operation,
            # rather than waiting for the next model/tool boundary to notice it.
            await asyncio.wait({call, watch}, return_when=asyncio.FIRST_COMPLETED)
            if signal.is_set():
                return
            response = call.result()
        finally:
            for pending in (call, watch):
                if not pending.done():
                    pending.cancel()
            await asyncio.gather(call, watch, return_exceptions=True)
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
                    signature=getattr(block, "signature", None) or getattr(block, "_signature", None),
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


class ForgeInterrupted(RuntimeError):
    """Signal-driven interruption completed Cell teardown and released its claim."""


class ForgeExecutor:
    def __init__(self, client) -> None:
        self._client = client

    async def run(
        self, *, job_id: str, role: str, task: str, workspace: str,
        model_ref: str, emit: Callable[[dict], None], inputs: list[dict] | None = None,
        signal: asyncio.Event | None = None, inbox=None,
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
        decoded: list[tuple[Path, bytes]] = []
        total_bytes = 0
        if inputs:
            try:
                for index, item in enumerate(inputs, 1):
                    name = PurePosixPath(str(item.get("name") or f"input-{index}").replace("\\", "/")).name
                    if name in {"", ".", ".."}:
                        name = f"input-{index}"
                    destination = input_dir / name
                    if destination in {entry[0] for entry in decoded}:
                        destination = input_dir / f"{index}-{name}"
                        while destination in {entry[0] for entry in decoded}:
                            destination = input_dir / ("_" + destination.name)
                    if item.get("artifact_hash"):
                        from forge.artifacts import ArtifactStore
                        if not settings.forge_workspace_root:
                            raise ValueError("Persistent original input storage is not configured")
                        data = await asyncio.to_thread(
                            ArtifactStore(Path(settings.forge_workspace_root)).read, item["artifact_hash"],
                        )
                    else:
                        data = base64.b64decode(str(item.get("data") or ""), validate=True)
                    total_bytes += len(data)
                    if len(data) > 64 * 1024 * 1024 or total_bytes > 128 * 1024 * 1024:
                        raise ValueError("Too many retained input bytes for one worker; select fewer Task input_ids")
                    decoded.append((destination, data))
                    materialized.append(str(destination.relative_to(workspace_path)))
            except (ValueError, binascii.Error, OSError) as exc:
                raise ValueError(f"could not materialize uploaded file {name}: {exc}") from exc

        @asynccontextmanager
        async def setup_inputs():
            input_dir.mkdir(parents=True, exist_ok=False)
            try:
                for destination, data in decoded:
                    destination.write_bytes(data)
                yield
            finally:
                shutil.rmtree(input_dir)

        if materialized:
            task = (
                f"{task}\n\nMark VI materialized the original uploaded files at these "
                f"workspace-relative paths:\n- " + "\n- ".join(materialized)
            )

        tool_names: dict[str, str] = {}

        async def publish(payload):
            published = emit(payload)
            if inspect.isawaitable(published):
                await published

        async def forward(event) -> None:
            data = event.data
            if event.type == "chunk" and data:
                await publish({"phase": "text", "text": str(data), "source": "forge"})
            elif event.type == "tool" and isinstance(data, dict):
                call_id = data.get("id")
                tool_name = data.get("name")
                if call_id and tool_name:
                    tool_names[str(call_id)] = str(tool_name)
                await publish({
                    "phase": "tool", "tool": tool_name,
                    "tool_call_id": call_id, "input": data.get("input"),
                    "source": "forge",
                })
            elif event.type == "tool_result" and isinstance(data, dict):
                content = data.get("content", data.get("result", ""))
                call_id = data.get("tool_use_id", data.get("id"))
                preview = str(content)[:1500]
                forwarded = {
                    "phase": "tool_result",
                    "tool_call_id": call_id,
                    "tool": data.get("name") or tool_names.get(str(call_id)),
                    "result": preview, "source": "forge",
                    "full_result": content,
                }
                if data.get("is_error"):
                    forwarded["error"] = preview
                await publish(forwarded)
            elif event.type in {"steered", "usage", "compact", "error"}:
                await publish({"phase": event.type, "data": data, "source": "forge"})

        images = {
            "coder": settings.forge_coder_image,
            "reviewer": settings.forge_reviewer_image,
            "pentester": settings.forge_pentester_image,
        }

        from dataclasses import replace
        from forge.config import ForgeSettings
        runtime_settings = ForgeSettings.from_env()
        if settings.forge_workspace_root:
            runtime_settings = replace(
                runtime_settings,
                workspace_root=Path(settings.forge_workspace_root).expanduser().resolve(),
            )
        result = await execute(
                ExecutionSpec(
                    job_id=job_id,
                    role=role,
                    task=task,
                    workspace=workspace_path,
                    model_ref=model_ref,
                    max_iterations=settings.forge_worker_max_iterations,
                    timeout_s=settings.forge_worker_timeout_s,
                    allow_network={
                        "coder": settings.forge_coder_allow_network,
                        "reviewer": settings.forge_reviewer_allow_network,
                        "pentester": settings.forge_pentester_allow_network,
                    }.get(role, False),
                    cell_backend=settings.forge_cell_backend,
                    cell_image=images[role],
                ),
                model=IgorModelAdapter(self._client, model_ref),
                emit=forward,
                settings=runtime_settings,
                **({"signal": signal} if signal is not None else {}),
                **({"inbox": inbox} if inbox is not None else {}),
                **({"workspace_setup": setup_inputs} if inputs else {}),
            )
        if result.status == "cancelled":
            raise ForgeInterrupted(result.report or "Forge execution interrupted")
        if result.status != "succeeded":
            raise RuntimeError(result.error or result.report or f"Forge job {result.status}")
        return result.report or "(Forge completed without a text report)"
