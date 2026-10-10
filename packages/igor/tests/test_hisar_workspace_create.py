# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers.hisar import CreateDirectoryRequest, create_hisar_dir
from app.services.hisar_workspaces import HisarWorkspaceService


def _request(root):
    service = HisarWorkspaceService(str(root), None, configured=False)
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(hisar_workspaces=service)))


async def test_create_workspace_directory_and_inherit_root_group(tmp_path, monkeypatch):
    root = tmp_path / "workspaces"
    parent = root / "optimus"
    parent.mkdir(parents=True)
    chowns = []
    monkeypatch.setattr(
        "app.services.hisar_workspaces.os.chown", lambda *args: chowns.append(args), raising=False
    )

    result = await create_hisar_dir(
        _request(root),
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

    with pytest.raises(HTTPException) as exc:
        await create_hisar_dir(
            _request(root),
            CreateDirectoryRequest(path="/Forge/workspaces", name=name),
        )

    assert exc.value.status_code == 422


async def test_create_workspace_directory_rejects_non_forge_parent(tmp_path, monkeypatch):
    root = tmp_path / "workspaces"
    root.mkdir()

    with pytest.raises(HTTPException) as exc:
        await create_hisar_dir(
            _request(root),
            CreateDirectoryRequest(path="/Documents", name="nope"),
        )

    assert exc.value.status_code == 403


async def test_create_workspace_directory_reports_duplicate(tmp_path, monkeypatch):
    root = tmp_path / "workspaces"
    root.mkdir()
    (root / "existing").mkdir()

    with pytest.raises(HTTPException) as exc:
        await create_hisar_dir(
            _request(root),
            CreateDirectoryRequest(path="/Forge/workspaces", name="existing"),
        )

    assert exc.value.status_code == 409


async def test_create_workspace_directory_refuses_coordinator_metadata(tmp_path):
    root = tmp_path / "workspaces"
    (root / ".forge").mkdir(parents=True)
    with pytest.raises(HTTPException) as exc:
        await create_hisar_dir(_request(root), CreateDirectoryRequest(path="/Forge/workspaces/.forge", name="nope"))
    assert exc.value.status_code == 403
    assert not (root / ".forge" / "nope").exists()
