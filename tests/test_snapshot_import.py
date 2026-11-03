import hashlib
import json
import sqlite3

import pytest
from sqlalchemy import select

from app.models.memory_serving import MemoryChange
from app.services.memory_runtime import MemoryRuntime, packet_json
from scripts.import_memory_snapshot import import_documents, import_history, read_snapshot


async def test_offline_document_import_preserves_source_and_stable_identity(memory, tmp_path):
    path = tmp_path / "original.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE memory_files(id INTEGER,user_id INTEGER,path TEXT,content TEXT,updated_at TEXT);
            CREATE TABLE memory_entities(id TEXT,user_id INTEGER,canonical_name TEXT,entity_type TEXT,aliases TEXT);
            CREATE TABLE memory_record_meta(memory_file_id INTEGER,user_id INTEGER,record_id TEXT,entity_id TEXT);
            CREATE TABLE memory_entity_heads(user_id INTEGER,entity_id TEXT,current_edition_id TEXT);
        """)
        db.execute("INSERT INTO memory_files VALUES(1,1,?,?,?)", (
            "/memories/social/ilayda.md", "# İlayda\n\nİlayda is the owner's sister; Erdem is her husband.", "2026-10-01"))
        db.execute("INSERT INTO memory_entities VALUES('person-1',1,'İlayda Bayrak','person',?)", (json.dumps(["Ilayda"]),))
        db.execute("INSERT INTO memory_record_meta VALUES(1,1,'record-1','person-1')")
        db.execute("INSERT INTO memory_entity_heads VALUES(1,'person-1','record-1')")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    snapshot = read_snapshot(path, 1)
    report = await import_documents(snapshot, memory.store, memory.context())
    assert report["files"][0]["record_id"] == "record-1"
    assert report["files"][0]["disposition"] == "published"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    packet = await MemoryRuntime(memory.store).prepare(memory.context("Ilayda and Erdem"))
    assert "owner's sister" in packet_json(packet)
    # Resuming the same immutable snapshot cannot duplicate its original sources.
    await import_documents(snapshot, memory.store, memory.context())
    async with memory.sessions() as db:
        assert len((await db.execute(select(MemoryChange))).scalars().all()) == 1


def test_snapshot_with_wal_is_rejected(tmp_path):
    path = tmp_path / "source.db"
    path.write_bytes(b"snapshot")
    (tmp_path / "source.db-wal").write_bytes(b"uncheckpointed")
    with pytest.raises(ValueError, match="WAL"):
        read_snapshot(path, 1)


async def test_history_import_indexes_context_and_keeps_inferences_out_of_facts(memory,tmp_path):
    path=tmp_path/"history.db"
    with sqlite3.connect(path) as db:
        db.executescript("""
        CREATE TABLE sessions(id INTEGER,user_id INTEGER,triggered_by TEXT);
        CREATE TABLE messages(id INTEGER,session_id INTEGER,role TEXT,content TEXT,created_at TEXT);
        CREATE TABLE observations(id INTEGER,user_id INTEGER,content TEXT,level TEXT,created_at TEXT,deleted_at TEXT,superseded_by INTEGER);
        INSERT INTO sessions VALUES(77,1,'user');
        """)
        db.execute("INSERT INTO messages VALUES(1,77,'assistant',?,'2026-10-01 10:00:00')",(json.dumps([{"type":"text","text":"Did Nora cancel the visit?"}]),))
        db.execute("INSERT INTO messages VALUES(2,77,'user',?,'2026-10-01 10:01:00')",(json.dumps([{"type":"text","text":"Yes, she did."}]),))
        db.execute("INSERT INTO observations VALUES(1,1,'Nora secretly dislikes everybody.','inferred','2026-10-01 11:00:00',NULL,NULL)")
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    snapshot={"path":str(path),"sha256":before,"source_user_id":1}
    counts=await import_history(snapshot,memory.store,memory.context())
    assert counts=={"owner_messages":1,"observation_claims":1}
    packet=await MemoryRuntime(memory.store).prepare(memory.context("Nora visit"))
    assert "Yes, she did." in packet_json(packet)
    assert "Did Nora cancel" in packet_json(packet)
    assert "secretly dislikes" not in packet_json(packet)
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before
    async with memory.sessions() as db:
        sources=(await db.execute(select(MemoryChange))).scalars().all()
        assert all(source.message_id is None and source.outcome=="source_only" for source in sources)


async def test_archive_preserved_but_not_published_as_current(memory):
    snapshot={"sha256":"test-snapshot", "files":[{"id":91,"path":"/memories/.archive/social/nora.md",
        "content":"# Nora\n\nNora was engaged years ago."}],"entities":{},"meta":{},"heads":{}}
    report=await import_documents(snapshot,memory.store,memory.context())
    assert report["files"][0]["disposition"]=="preserved_source_only"
    packet=await MemoryRuntime(memory.store).prepare(memory.context("Nora"))
    assert "was engaged" not in packet_json(packet)
