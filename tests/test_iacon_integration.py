import json
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import create_app
from app.config import settings
from app.services.iacon_catalogue import Catalogue
from app.services.memory_runtime import MemoryRuntime, packet_json
from scripts.import_memory_snapshot import import_documents
from scripts.import_iacon_catalogue import import_catalogue, legacy_state
from sqlalchemy import select
from app.models.memory_serving import MemoryChange, MemoryServingUnit
from app.schemas.iacon import Command
from app.services.memory_store import capture_source


async def test_authenticated_routes_lifespan_and_old_orion_trigger_use_gate():
    app = create_app()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            assert (await http.get("/iacon/maintenance")).status_code == 401
            headers = {"X-API-Key": settings.speda_api_key}
            assert (await http.post("/iacon/drain", headers=headers)).status_code == 401
            source = await http.post("/iacon/sources", headers=headers, json={"key": "integration", "text": "I like cycling."})
            assert source.status_code == 200
            row = await http.post("/iacon/records", headers=headers, json={"command_id": "integration-create",
                "kind": "preference", "title": "Exercise", "payload": {"text": "I like cycling."},
                "evidence": [{"source_id": source.json()["source_id"], "start": 0, "end": 15, "quote": "I like cycling."}]})
            assert row.status_code == 200, row.text
            assert (await http.get("/iacon/records/"+row.json()["id"], headers=headers)).status_code == 200
            headers["X-N8N-Secret"] = settings.n8n_secret
            result = await http.post("/trigger/orion", headers=headers, json={"payload": {"type": "nightly_audit"}, "output_mode": "silent"})
            assert result.status_code == 200, result.text
            status = (await http.get("/iacon/maintenance", headers=headers)).json()
            assert status["reservations"] == []


async def test_offline_native_documents_and_states_preserve_originals(memory):
    record = {"key": "application", "summary": "Waiting for application", "status": "waiting",
        "source": "owner conversation", "review_on": "2026-10-01", "verified_on": "2026-09-28"}
    content = (f'# application\n\n<!-- memory-state-v1 {json.dumps(record, ensure_ascii=False, sort_keys=True)} -->\n\n'
        'Waiting for application\n\n- Status: waiting\n- Verified: 2026-09-28\n- Review: 2026-10-01\n- Source: owner conversation\n')
    snapshot = {"sha256": "offline", "entities": {}, "meta": {}, "heads": {}, "files": [
        {"id": 1, "path": "/memories/states/09-26/application.md", "content": content},
        {"id": 2, "path": "/memories/finance/ledger.md", "content": "# Ledger\n\n|entry|amount|\n|A|100|"},
        {"id": 3, "path": "/memories/.archive/old.md", "content": "Old unsupported claim"}]}
    await import_documents(snapshot, memory.store, memory.context())
    report = await import_catalogue(snapshot, memory.store, memory.context())
    assert report["states"] == 1 and report["documents"] == 3
    rows = await Catalogue(memory.store).list(1)
    state = next(r for r in rows if r["kind"] == "state")
    assert state["payload"]["verified_on"] == "2026-09-28" and state["payload"]["status"] == "waiting"
    assert next(r for r in rows if r["title"].endswith("ledger.md"))["payload"]["content"] == snapshot["files"][1]["content"]
    async with memory.sessions() as db:
        unit = await db.scalar(select(MemoryServingUnit).where(MemoryServingUnit.stable_key == "iacon:" + state["id"]))
        assert unit and unit.kind == "situation" and any(d.get("source_only_state") for d in unit.dependencies)
    packet = await MemoryRuntime(memory.store).prepare(memory.context("Old unsupported claim"))
    assert "Old unsupported claim" not in packet_json(packet)
    with pytest.raises(ValueError, match="disagree"):
        legacy_state(content + "tampered")


async def test_migrated_entity_never_promotes_archived_edition(memory):
    snapshot = {"sha256": "editions", "entities": {"nora": {"id": "nora", "canonical_name": "Nora",
        "entity_type": "person", "aliases": []}}, "meta": {1: {"record_id": "old", "entity_id": "nora"},
        2: {"record_id": "current", "entity_id": "nora"}}, "heads": {"nora": "current"}, "files": [
        {"id": 1, "path": "/memories/social/old.md", "content": "Nora is engaged. ARCHIVED_UNSUPPORTED"},
        {"id": 2, "path": "/memories/social/current-edition.md", "content": "Nora is now married."}]}
    await import_documents(snapshot, memory.store, memory.context())
    await import_catalogue(snapshot, memory.store, memory.context())
    packet = await MemoryRuntime(memory.store).prepare(memory.context("Nora"))
    entities = await Catalogue(memory.store).list(1, kind="entity")
    assert entities[0]["evidence"][0]["quote"] == "Nora is now married."
    # Older source evidence may still be recalled, but must retain its historical qualifier.
    for block in packet.blocks:
        if "ARCHIVED_UNSUPPORTED" in block:
            assert "historical; not evidence of current status" in json.loads(block)["warnings"]
    assert "now married" in packet_json(packet)


@pytest.mark.parametrize("scope", [
    {"historical": True},
    {"eligible_for_context": False},
    {"valid_from": "2025-01-01", "valid_until": "2025-02-01"},
])
async def test_projection_inherits_evidence_currency(memory, scope):
    async with memory.sessions() as db, db.begin():
        source = await capture_source(db, user_id=1, source_key="old:evidence",
            content="Nora is engaged.", source_role="canonical", provenance=scope)
        source_id = source.id
    catalogue = Catalogue(memory.store)
    record = await catalogue.apply(1, Command(command_id="old:document", kind="document",
        title="Nora", payload={"content": "Nora is engaged."},
        evidence=[{"source_id": source_id, "start": 0, "end": 16, "quote": "Nora is engaged."}]), actor="migration")
    # Rebuilding a disposable view must preserve the restriction as well.
    await catalogue.refresh(1, record["id"])
    result = await catalogue.get(1, record["id"])
    async with memory.sessions() as db:
        unit = (await db.execute(select(MemoryServingUnit).where(
            MemoryServingUnit.stable_key == "iacon:" + record["id"]))).scalar_one_or_none()
        generated = (await db.execute(select(MemoryChange).where(
            MemoryChange.source_key.like("iacon:%"), MemoryChange.revoked.is_(False)))).scalar_one()
    assert generated.provenance.items() >= scope.items()
    if scope.get("eligible_for_context") is False:
        assert unit is None
    else:
        assert unit
        for key, value in scope.items():
            assert unit.sections[0][key] == value
    for key, value in scope.items():
        assert result["evidence_scope"][key] == value


async def test_pre_scope_projection_is_safe_before_background_refresh(memory):
    from types import SimpleNamespace
    from app.services.memory_store import scoped_sections
    original = SimpleNamespace(id=1, provenance={"historical": True, "valid_until": "2025-02-01"})
    generated = SimpleNamespace(id=2, provenance={"iacon_record": "old"})
    unit = SimpleNamespace(stable_key="iacon:old", dependencies=[{"source_id":1}, {"source_id":2}],
        sections=[{"source_id":2, "text":"Obsolete summary", "historical":False}])
    scoped = scoped_sections(unit, {1:original, 2:generated})
    assert scoped[0]["historical"] and scoped[0]["valid_until"] == "2025-02-01"
    assert unit.sections[0]["historical"] is False  # Original view/evidence stays untouched.


async def test_explicit_catalogue_excerpt_keeps_historical_qualifiers(memory):
    from app.skills.memory_lookup import MemoryLookupSkill
    text = "Nora is engaged."
    async with memory.sessions() as db, db.begin():
        source = await capture_source(db, user_id=1, source_key="archive:document", content=text,
            source_role="canonical", provenance={"historical":True, "eligible_for_context":False})
    catalogue = Catalogue(memory.store)
    record = await catalogue.apply(1, Command(command_id="archive:create", kind="document", title="Old edition",
        payload={"content":text}, evidence=[{"source_id":source.id, "start":0, "end":len(text), "quote":text}]), actor="migration")
    skill = MemoryLookupSkill(MemoryRuntime(memory.store), catalogue=catalogue)
    result = json.loads(await skill.execute({"query":"Nora", "record_id":record["id"]}, memory.context()))
    assert result["text"] == text and result["evidence_scope"]["historical"]
    assert "historical; not evidence of current status" in result["warnings"]
