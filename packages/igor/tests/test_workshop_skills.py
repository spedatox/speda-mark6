import json
from types import SimpleNamespace

from app.skills.workshop import WorkshopStatusSkill, WorkshopUpdateSkill


async def test_select_checkpoint_and_reopen_from_another_agent(tmp_path, monkeypatch):
    monkeypatch.setattr("app.execution.forge.settings.forge_workspace_root", str(tmp_path))
    workspace = tmp_path / "editor"
    workspace.mkdir()
    optimus = SimpleNamespace(agent_id="optimus", extra={})
    update = WorkshopUpdateSkill(str(tmp_path))
    selected = json.loads(await update.execute({"action": "select", "workspace": str(workspace)}, optimus))
    assert optimus.extra["cwd"] == str(workspace.resolve())
    checkpoint = json.loads(await update.execute({
        "action": "checkpoint", "project_id": selected["id"], "revision": 0,
        "state": {"objective": "Build editor", "status": "active", "next_steps": ["Fix undo"]},
    }, optimus))
    assert checkpoint["revision"] == 1
    speda = SimpleNamespace(agent_id="speda", extra={})
    inspected = json.loads(await WorkshopStatusSkill(str(tmp_path)).execute({"project_id": selected["id"]}, speda))
    assert inspected["checkpoint"]["next_steps"] == ["Fix undo"]
    assert speda.extra == {}
    selected_again = json.loads(await update.execute({"action": "select", "project_id": selected["id"]}, speda))
    assert selected_again["selected_for_this_turn"]
    assert speda.extra["cwd"] == str(workspace.resolve())


async def test_outside_root_selection_leaves_context_unchanged(tmp_path, monkeypatch):
    root = tmp_path / "allowed"
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setattr("app.execution.forge.settings.forge_workspace_root", str(root))
    context = SimpleNamespace(extra={"cwd": "previous"})
    result = await WorkshopUpdateSkill(str(root)).execute({"action": "select", "workspace": str(outside)}, context)
    assert "refused" in result
    assert context.extra["cwd"] == "previous"


async def test_status_is_readonly_and_missing_checkpoint_revision_is_rejected(tmp_path):
    context = SimpleNamespace(extra={"cwd": str(tmp_path)})
    status = await WorkshopStatusSkill(str(tmp_path)).execute({}, context)
    assert "not been initialized" in status
    assert not (tmp_path / ".forge").exists()
    result = await WorkshopUpdateSkill(str(tmp_path)).execute({"action": "checkpoint", "state": {}}, context)
    assert "revision" in result
