"""Workspace confinement and cancellation of real CLI process handles."""
import asyncio
import sys
from unittest.mock import AsyncMock

import pytest

from forge.cell.base import CellPolicy
from forge.cell.docker_cell import DockerCell, DockerError


@pytest.mark.parametrize("path", ["../outside", "foo/../../outside", "/workspace/../outside", "/workspace-other/a"])
def test_container_guard_rejects_traversal_on_any_host_os(path):
    cell = DockerCell("test", "fixture", CellPolicy())
    with pytest.raises(PermissionError):
        cell._guard(path)


def test_container_guard_normalizes_contained_paths_and_tightens_subdirectory():
    cell = DockerCell("test", "fixture", CellPolicy())
    assert cell._guard("a/../b") == "/workspace/b"
    cell._subpath = "job"
    assert cell._guard("b") == "/workspace/job/b"
    with pytest.raises(PermissionError):
        cell._guard("../other")


@pytest.mark.parametrize("write", [False, True])
async def test_container_symlink_escape_is_rejected_before_file_operation(write):
    cell = DockerCell("test", "fixture", CellPolicy())
    cell._docker = AsyncMock(return_value=(0, b"/workspace\0/outside/file\0", b""))
    with pytest.raises(PermissionError):
        if write:
            await cell.write("link/file", "must not write")
        else:
            await cell.read("link/file")
    assert cell._docker.await_count == 1
    assert "realpath" in cell._docker.call_args.args


async def test_container_cannot_skip_confinement_if_realpath_is_unavailable():
    cell = DockerCell("test", "fixture", CellPolicy())
    cell._docker = AsyncMock(return_value=(127, b"", b"missing realpath"))
    with pytest.raises(DockerError, match="could not verify"):
        await cell.write("file", "must not write")
    assert cell._docker.await_count == 1


async def test_active_subdirectory_symlink_cannot_redefine_workspace_boundary():
    cell = DockerCell("test", "fixture", CellPolicy())
    cell._subpath = "job"
    cell._docker = AsyncMock(return_value=(0, b"/outside\0/outside/file\0", b""))
    with pytest.raises(PermissionError):
        await cell.read("file")


@pytest.mark.parametrize("operation", ["stream", "stdin", "readiness"])
async def test_cancel_reaps_real_cli_process_and_readers(monkeypatch, operation):
    """Codex owns tool dispatch; dropping an await must not orphan the CLI."""
    ready = asyncio.Event()
    processes = []
    spawn = asyncio.create_subprocess_exec

    async def stand_in_cli(*args, **kwargs):
        process = await spawn(sys.executable, "-c", "import time; time.sleep(60)", **kwargs)
        processes.append(process)
        ready.set()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", stand_in_cli)
    cell = DockerCell("test", "fixture", CellPolicy())
    task = asyncio.create_task(
        cell.available() if operation == "readiness"
        else cell._docker("exec", "fixture", stdin=b"input" if operation == "stdin" else None)
    )
    await asyncio.wait_for(ready.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert processes[0].returncode is not None
