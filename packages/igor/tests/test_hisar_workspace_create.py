# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers.hisar import CreateDirectoryRequest, create_hisar_dir


async def test_create_workspace_directory_and_inherit_root_group(tmp_path, monkeypatch):
    root = tmp_path / "workspaces"
    parent = root / "optimus"
    parent.mkdir(parents=True)
    monkeypatch.setattr("app.routers.hisar.settings.forge_workspace_root", str(root))
    chowns = []
    monkeypatch.setattr(
        "app.routers.hisar.os.chown", lambda *args: chowns.append(args), raising=False
    )

    result = await create_hisar_dir(
        SimpleNamespace(),
        CreateDirectoryRequest(path="/Forge/workspaces/optimus", name="big-project"),
    )

    created = parent / "big-project"
    assert created.is_dir()
    assert result == {
        "path": "/Forge/workspaces/optimus/big-project",
        "name": "big-project",
    }
    if chowns:
        assert chowns[0][0] == created
        assert chowns[0][2] == root.stat().st_gid


@pytest.mark.parametrize("name", ["", ".hidden", "../escape", "a/b", "a\\b"])
async def test_create_workspace_directory_rejects_unsafe_names(tmp_path, monkeypatch, name):
    root = tmp_path / "workspaces"
    root.mkdir()
    monkeypatch.setattr("app.routers.hisar.settings.forge_workspace_root", str(root))

    with pytest.raises(HTTPException) as exc:
        await create_hisar_dir(
            SimpleNamespace(),
            CreateDirectoryRequest(path="/Forge/workspaces", name=name),
        )

    assert exc.value.status_code == 422


async def test_create_workspace_directory_rejects_non_forge_parent(tmp_path, monkeypatch):
    root = tmp_path / "workspaces"
    root.mkdir()
    monkeypatch.setattr("app.routers.hisar.settings.forge_workspace_root", str(root))

    with pytest.raises(HTTPException) as exc:
        await create_hisar_dir(
            SimpleNamespace(),
            CreateDirectoryRequest(path="/Documents", name="nope"),
        )

    assert exc.value.status_code == 403


async def test_create_workspace_directory_reports_duplicate(tmp_path, monkeypatch):
    root = tmp_path / "workspaces"
    root.mkdir()
    (root / "existing").mkdir()
    monkeypatch.setattr("app.routers.hisar.settings.forge_workspace_root", str(root))

    with pytest.raises(HTTPException) as exc:
        await create_hisar_dir(
            SimpleNamespace(),
            CreateDirectoryRequest(path="/Forge/workspaces", name="existing"),
        )

    assert exc.value.status_code == 409
