"""Linux Docker checks against a locally present image and temporary mounts.

Run explicitly with --image <immutable image ID>; no pull or deployment occurs.
Codex references: tools/parallel.rs owns dispatch, tasks/mod.rs settles cleanup,
and sandboxing/src/bwrap.rs treats filesystem boundaries explicitly. Only the
standard-library Cell modules are loaded; no agent, model or production database
is initialized. Existing Cells and workshop claims are never touched.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types


def load_cell_modules():
    package_root = Path(__file__).resolve().parents[2] / "forge"
    # The normal package initializer also imports the configured Cell factory.
    # This isolated check needs neither its optional dependencies nor settings.
    for name, path in (("forge", package_root), ("forge.cell", package_root / "cell")):
        package = types.ModuleType(name)
        package.__path__ = [str(path)]
        sys.modules[name] = package
    from forge.cell.base import CellPolicy
    from forge.cell.docker_cell import DockerCell
    return DockerCell, CellPolicy, package_root


def confirm_removed(name):
    result = subprocess.run(["docker", "container", "inspect", name], capture_output=True, text=True)
    if result.returncode == 0 or not any(
        marker in result.stderr for marker in ("No such object", "No such container")
    ):
        raise RuntimeError(f"Could not confirm removal of validation Cell {name}")


async def check(image):
    DockerCell, CellPolicy, package_root = load_cell_modules()
    assert await DockerCell.available(), "Docker daemon unavailable"
    scratch = Path(tempfile.mkdtemp(prefix="forge-docker-reliability-")).resolve()
    cells, removed, checks = [], set(), []

    async def deny(cell, path, operation, label):
        try:
            if operation == "read":
                await cell.read(path)
            else:
                await cell.write(path, "must not escape")
        except PermissionError:
            checks.append(label)
        else:
            raise AssertionError(f"{label} did not reject the path")

    try:
        for lab_root in (False, True):
            posture = "root" if lab_root else "uid1000"
            workspace = scratch / posture
            workspace.mkdir()
            cell = DockerCell("codex-reliability", image,
                              CellPolicy(memory_mb=512, cpus=0.5, run_as_root=lab_root),
                              workspace_mount=workspace)
            cells.append(cell)
            try:
                await cell.start()
                await cell.write("nested/../inside.txt", "retained")
                await cell.write("new/parents/file.txt", "new destination")
                assert await cell.read("inside.txt") == "retained"
                assert await cell.read("new/parents/file.txt") == "new destination"
                checks.append(f"{posture}: normalized paths and nonexistent parents")

                for path in ("../../etc/passwd", "/etc/passwd", "/workspace-other/file"):
                    for operation in ("read", "write"):
                        await deny(cell, path, operation, f"{posture}: {operation} rejects {path}")

                setup = await cell.run(
                    "printf original > /tmp/codex-validation-outside; "
                    "ln -s /tmp/codex-validation-outside outside-file; "
                    "ln -s /tmp outside-parent; "
                    "ln -s /tmp active; "
                    "ln -s inside.txt inside-link"
                )
                assert setup.exit_code == 0, setup.stderr
                for path in ("outside-file", "outside-parent/new-destination"):
                    for operation in ("read", "write"):
                        await deny(cell, path, operation, f"{posture}: {operation} rejects {path}")
                assert await cell.read("inside-link") == "retained"
                unchanged = await cell.run("cat /tmp/codex-validation-outside")
                assert unchanged.exit_code == 0 and unchanged.stdout == "original"
                checks.append(f"{posture}: contained link works and external target unchanged")

                cell.enter_subpath("active")
                try:
                    for operation in ("read", "write"):
                        await deny(cell, "file", operation, f"{posture}: {operation} rejects escaped active directory")
                finally:
                    cell.leave_subpath()

                ready = asyncio.Event()

                def output(stream, text):
                    if stream == "stdout" and "COMMAND_READY" in text:
                        ready.set()

                command = asyncio.create_task(cell.run(
                    "python -u -c 'import time; print(\"COMMAND_READY\"); time.sleep(30)'",
                    on_output=output,
                ))
                try:
                    await asyncio.wait_for(ready.wait(), 10)
                    command.cancel()
                    try:
                        await asyncio.wait_for(command, 10)
                    except asyncio.CancelledError:
                        pass
                    else:
                        raise AssertionError("Command did not propagate cancellation")
                finally:
                    if not command.done():
                        command.cancel()
                    await asyncio.gather(command, return_exceptions=True)
                checks.append(f"{posture}: live command cancellation collected CLI/readers")
            finally:
                await cell.close()
                confirm_removed(cell.container)
                removed.add(cell.container)
                checks.append(f"{posture}: Cell removal confirmed")
        print(json.dumps({"ok": True, "image": image,
                          "candidate_sha256": hashlib.sha256((package_root / "cell/docker_cell.py").read_bytes()).hexdigest(),
                          "checks": checks, "cells_removed": sorted(removed)}, indent=2))
    finally:
        if all(cell.container in removed for cell in cells):
            shutil.rmtree(scratch)
        else:
            print(f"Unconfirmed cleanup: keep {scratch} for operator inspection", file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="Already present sha256 image ID; no mutable tag or pull.")
    args = parser.parse_args()
    if os.name != "posix" or not args.image.startswith("sha256:"):
        parser.error("Run on Linux with an immutable local image ID")
    inspect = subprocess.run(["docker", "image", "inspect", args.image], capture_output=True)
    if inspect.returncode != 0:
        parser.error("Image is not locally present")
    asyncio.run(check(args.image))
