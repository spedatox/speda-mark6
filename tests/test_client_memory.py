import json
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, func
from app.config import settings
from app.main import create_app
from app.models.iacon import IaconRecord, IaconRevision, IaconFollowup
from app.models.memory_serving import MemoryChange
from app.profiles.registry import ProfileRegistry
from app.schemas.iacon import Command
from app.services.client_memory import ClientMemory
from app.services.iacon_catalogue import Catalogue, Conflict
from app.services.memory_store import capture_source
from scripts.import_memory_snapshot import import_documents
from scripts.import_iacon_catalogue import import_catalogue


async def test_editor_http_cas_revisions_restore_and_owner_scope(memory):
    catalogue = Catalogue(memory.store)
    editor = ClientMemory(catalogue)
    app = create_app()
    app.state.client_memory = editor
    app.state.profiles = ProfileRegistry()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"X-API-Key": settings.speda_api_key}) as http:
        assert (await http.get("/memory/files", headers={"X-API-Key": "wrong"})).status_code == 401
        created = await http.put("/memory/files", json={"path": "/memories/life/bike.md", "content": "Original"})
        assert created.status_code == 200, created.text
        first = created.json()
        assert first["display_title"] is None
        updated = await http.put("/memory/files", json={"path": first["path"], "content": "Corrected",
            "expected_updated_at": first["updated_at"]})
        assert updated.status_code == 200, updated.text
        stale = await http.put("/memory/files", json={"path": first["path"], "content": "Stale",
            "expected_updated_at": first["updated_at"]})
        assert stale.status_code == 409 and stale.json()["detail"]["current"]["content"] == "Corrected"
        history = (await http.get("/memory/files/revisions", params={"path": first["path"]})).json()
        assert len(history) == 2 and history[0]["before"] == "Original" and history[0]["after"] == "Corrected"
        moved = await http.post("/memory/files/rename", json={"old_path": first["path"],
            "new_path": "/memories/life/cycling.md"})
        assert moved.status_code == 200 and moved.json()["record_id"] == first["record_id"]
        retired = await http.delete("/memory/files", params={"path": moved.json()["path"]})
        assert retired.status_code == 200 and retired.json()["recoverable"]
        assert (await http.get("/memory/files")).json() == []
        restored = await http.post("/memory/files/restore", json={"revision_id": history[-1]["id"]})
        assert restored.status_code == 200, restored.text
        assert restored.json()["content"] == "Original" and restored.json()["version"] == 5
        assert restored.json()["path"] == moved.json()["path"]
        assert (await http.put("/memory/files", json={"path": "/memories/../escape.md", "content": "x"})).status_code == 422
        assert (await http.put("/memory/sources", json={"agent_id": "speda", "path": first["path"]})).status_code == 409
        assert not (await http.get("/memory/sources")).json()["assignable"]
    async with memory.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(IaconRecord)) == 1
        assert await db.scalar(select(func.count()).select_from(IaconRevision)) == 5
        originals = (await db.execute(select(MemoryChange).where(MemoryChange.source_key.like("owner-editor:%")))).scalars().all()
        assert any(s.content == "Original" for s in originals)
        assert all(s.source_role == 'quoted' for s in originals)  # A document's words are not owner assertions.
        assert all("Stale" not in s.content for s in originals)
    assert await editor.files(2) == []
    with pytest.raises(KeyError):
        await editor.restore(2, history[-1]["id"])


async def test_owner_state_controls_create_edit_close_and_fence_stale_writes(memory):
    app = create_app()
    catalogue = Catalogue(memory.store)
    app.state.client_memory = ClientMemory(catalogue)
    state = {"summary": "The service is still under attack.", "status": "active", "salience": "high",
             "verified_on": "2026-11-11", "review_on": "2026-11-12", "question": "Is the incident contained?"}
    create = {"request_id": "create-incident", "title": "Server incident", "state": state}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        assert (await http.put("/memory/states", json=create)).status_code == 401
        http.headers["X-API-Key"] = settings.speda_api_key
        first = (await http.put("/memory/states", json=create)).json()
        assert first["kind"] == "state" and first["version"] == 1
        assert (await http.put("/memory/states", json=create)).json()["record_id"] == first["record_id"]
        update = {"request_id": "close-incident", "title": "Server incident contained",
                  "record_id": first["record_id"], "expected_version": 1,
                  "state": {**state, "status": "completed", "closed_on": "2026-11-12",
                            "verified_on": "2026-11-12", "salience": "normal"}}
        closed = (await http.put("/memory/states", json=update)).json()
        assert closed["path"] == first["path"] and closed["version"] == 2
        assert closed["display_title"] == "Server incident contained"
        assert (await http.put("/memory/states", json={**update, "request_id": "stale-incident"})).status_code == 409
        assert (await http.put("/memory/states", json={**create, "state": {**state, "status": "completed"}})).status_code == 422
    history = await catalogue.get(1, first["record_id"], history=True)
    assert [row["payload"]["status"] for row in history["revisions"]] == ["completed", "active"]
    async with memory.sessions() as db:
        pending = await db.scalars(select(IaconFollowup).where(IaconFollowup.state_id == first["record_id"]))
        assert [row.status for row in pending] == ["superseded"]


async def test_imported_projections_read_only_and_state_edit_uses_lifecycle(memory):
    snapshot = {"sha256": "editor-import", "entities": {}, "meta": {}, "heads": {}, "files": [
        {"id": 1, "path": "/memories/finance/ledger.md", "content": "Immutable legacy ledger"},
        {"id": 2, "path": "/memories/.archive/old.md", "content": "Archive"}]}
    await import_documents(snapshot, memory.store, memory.context())
    await import_catalogue(snapshot, memory.store, memory.context())
    catalogue = Catalogue(memory.store)
    editor = ClientMemory(catalogue)
    files = await editor.files(1)
    assert len(files) == 1 and files[0]["path"] == snapshot["files"][0]["path"] and not files[0]["editable"]
    with pytest.raises(ValueError, match="read-only"):
        await editor.commit(1, files[0]["path"], "overwrite", files[0]["updated_at"])
    text = "I am waiting for an application."
    async with memory.sessions() as db, db.begin():
        source = await capture_source(db, user_id=1, source_key="owner:application", content=text)
    state = await catalogue.apply(1, Command(command_id="owner:state", kind="state", title="Application",
        payload={"summary": text, "status": "waiting", "verified_on": "2026-10-20", "review_on": "2026-10-25",
                 "question": "Any news about your application?"},
        evidence=[{"source_id": source.id, "start": 0, "end": len(text), "quote": text}]))
    view = next(v for v in await editor.files(1) if v["record_id"] == state["id"])
    assert view["display_title"] == "Application"
    assert view["path"] == f"/memories/iacon/state/{state['id']}.json"
    payload = json.loads(view["content"])
    payload.update(status="completed", closed_on="2026-10-24", verified_on="2026-10-24")
    result = await editor.commit(1, view["path"], json.dumps(payload), view["updated_at"])
    assert result["display_title"] == "Application" and result["path"] == view["path"]
    assert result["version"] == 2 and json.loads(result["content"])["status"] == "completed"
    async with memory.sessions() as db:
        followups = (await db.execute(select(IaconFollowup).where(IaconFollowup.state_id == state["id"]))).scalars().all()
        assert all(f.status == "superseded" for f in followups)
    assert (await catalogue.get(1, state["id"]))["evidence_scope"]["historical"]


async def test_rename_cannot_launder_validity_and_restore_requires_live_evidence(memory):
    catalogue = Catalogue(memory.store)
    editor = ClientMemory(catalogue)
    text = "Nora worked in Ankara."
    async with memory.sessions() as db, db.begin():
        source = await capture_source(db, user_id=1, source_key="dated:owner", content=text,
            provenance={"valid_from": "2025-01-01", "valid_until": "2025-02-01"})
    record = await catalogue.apply(1, Command(command_id="dated:create", kind="document", title="Dated",
        payload={"content": text}, evidence=[{"source_id": source.id, "start": 0, "end": len(text), "quote": text}]))
    first = (await editor.files(1))[0]
    await editor.rename(1, first["path"], "/memories/dated.md")
    assert (await catalogue.get(1, record["id"]))["evidence_scope"]["valid_until"] == "2025-02-01"
    history = await editor.revisions(1, "/memories/dated.md")
    # An explicit new correction can change dates, but restoring a dated edition keeps its bounds.
    current = (await editor.files(1))[0]
    await editor.commit(1, current["path"], "Nora works in Istanbul.", current["updated_at"])
    restored = await editor.restore(1, history[-1]["id"])
    assert restored["evidence_scope"]["valid_until"] == "2025-02-01"
    await memory.store.revoke(memory.context(), source.id)
    with pytest.raises(ValueError, match="withdrawn"):
        await editor.restore(1, history[-1]["id"])


async def test_corrected_import_does_not_leak_obsolete_prepared_text(memory):
    from app.services.memory_runtime import MemoryRuntime, packet_json
    snapshot = {"sha256": "corrected-import", "entities": {}, "meta": {}, "heads": {}, "files": [
        {"id": 1, "path": "/memories/life/work.md", "content": "I work in Ankara. OBSOLETE_OFFICE"}]}
    await import_documents(snapshot, memory.store, memory.context())
    await import_catalogue(snapshot, memory.store, memory.context())
    catalogue = Catalogue(memory.store)
    editor = ClientMemory(catalogue)
    # Check correction visibility independently of the runtime's latency tests.
    runtime = MemoryRuntime(memory.store, prepare_timeout=2.0)
    assert "OBSOLETE_OFFICE" in packet_json(await runtime.prepare(memory.context("OBSOLETE_OFFICE")))
    view = (await editor.files(1))[0]
    await editor.commit(1, view["path"], "I now work in Istanbul. CURRENT_OFFICE", view["updated_at"])
    packet = await runtime.prepare(memory.context("OBSOLETE_OFFICE CURRENT_OFFICE"))
    assert "OBSOLETE_OFFICE" not in packet_json(packet)
    assert "CURRENT_OFFICE" in packet_json(packet)
    async with memory.sessions() as db:
        original = await db.scalar(select(MemoryChange).where(MemoryChange.source_key.like("snapshot:%")))
        assert original.content == snapshot["files"][0]["content"] and not original.revoked
    # Explicit historical evidence lookup still works, but cannot masquerade as current.
    old = await runtime.lookup(memory.context("OBSOLETE_OFFICE"), "OBSOLETE_OFFICE", source_ids=[original.id])
    assert "OBSOLETE_OFFICE" in packet_json(old)
    assert "historical; not evidence of current status" in packet_json(old)
