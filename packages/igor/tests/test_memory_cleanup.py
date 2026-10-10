"""Incident-shaped cutover: conflicting editions, missing sources, restart and parity."""
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
import pytest
import app.models
from app.database import Base
from app.models.memory_file import MemoryFile
from app.models.memory_source import MemorySource
from app.models.memory_record_meta import MemoryRecordMeta
from app.services.memory_cleanup import CorpusCleanup, digest, file_hash, merge_documents
from app.services.memory_catalog import resolve_member
from app.services.memory_spec import collection_by_root
from app.services.memory_store import mutate_file, resolve_alias
from app.skills.memory import MemorySkill, MemoryRecallCache, recall_for_context


@pytest.fixture
def snapshot(tmp_path):
    path = tmp_path / "original.db"
    engine = create_engine("sqlite:///"+path.as_posix())
    Base.metadata.create_all(engine)
    engine.dispose()
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO users VALUES (1,'Owner','Europe/Istanbul','2026-09-01')")
        docs = [
            (1,"/memories/social/personal/bedirhan/09-26.md","# Bedirhan\n\n**Who:** Theology student, born 2007.\n\n**Events:**\n- [2026-09-27] Moved into the dorm.\n"),
            (2,"/memories/social/personal/bedirhan.md","# Bedirhan\n\n**Who:** From Kahramanmaras.\n\n**Events:**\n- [2026-09-28] Cinema visit.\n"),
            (3,"/memories/dossier/09-26/prohibitions.md","# Explicit prohibitions\n\n- Never call a guess a fact.\n"),
            (4,"/memories/.archive/old-owner.md","# Owner\n\nA unique historical detail not present anywhere else.\n"),
            (5,"/memories/.audit/ops.md","# Audit\n\nRepeated deterministic failure.\n"),
        ]
        db.executemany("INSERT INTO memory_files VALUES (?,1,?,?,'2026-09-28 12:00:00')",docs)
        db.execute("INSERT INTO memory_revisions (user_id,path,author,action,\"before\",\"after\",request_id,created_at) VALUES (1,'observation:123','orion','observation_repair','original','repair','old','2026-09-01')")
        # Orphan embedding is often the last surviving copy of source wording.
        db.execute("INSERT INTO message_embeddings VALUES (1,900,800,1,'speda','user','Only surviving message',?,'2026-09-01')",(b'\x00\x01',))
    return path


def test_lossless_cutover_resumes_without_repeating_success(snapshot,tmp_path):
    before=file_hash(snapshot)
    output=tmp_path/"clean.db"
    migration=CorpusCleanup(snapshot,output)
    assert migration.run(stop_after=1)["committed_this_run"]==1
    migration.close()
    migration=CorpusCleanup(snapshot,output)
    report=migration.run()
    assert report["source_unchanged"] and file_hash(snapshot)==before
    assert report["memory_files"]==2 and report["original_documents_preserved"]==5
    assert report["memory_sources"]==6 and report["foreign_key_violations"]==0
    db=migration.db
    content=db.execute("SELECT content FROM memory_files WHERE path='/memories/social/09-26/personal/bedirhan.md'").fetchone()[0]
    for text in ("Theology student", "born 2007", "Kahramanmaras", "Moved into the dorm", "Cinema visit"):
        assert text in content
    assert content.count("# Bedirhan")==1 and content.count("**Who:**")==1
    assert db.execute("SELECT count(*) FROM memory_record_meta").fetchone()[0]==2
    assert db.execute("SELECT count(*) FROM memory_entity_heads").fetchone()[0]==1
    assert not db.execute("SELECT 1 FROM memory_graph_edges WHERE target_ref='path:observation:123'").fetchone()
    revisions=db.execute("SELECT count(*) FROM memory_revisions").fetchone()[0]
    heads=db.execute("SELECT version FROM memory_entity_heads").fetchone()[0]
    migration.run()
    assert db.execute("SELECT count(*) FROM memory_revisions").fetchone()[0]==revisions
    assert db.execute("SELECT version FROM memory_entity_heads").fetchone()[0]==heads
    assert json.loads(db.execute("SELECT original_row FROM memory_sources WHERE classification='orphan_message'").fetchone()[0])["embedding"]=={"base64":"AAE="}
    migration.close()


def test_in_place_cutover_is_forbidden(snapshot):
    with pytest.raises(ValueError,match="in-place"):
        CorpusCleanup(snapshot,snapshot)


def test_cleanup_reuses_existing_canonical_entity(snapshot, tmp_path):
    with sqlite3.connect(snapshot) as db:
        db.execute("INSERT INTO memory_entities VALUES (?,?,?,?,?,?,?)",
                   ("existing-identity", 1, "social", "person", "Bedirhan", "[]", "2026-09-01"))
    migration = CorpusCleanup(snapshot, tmp_path / "identities.db")
    try:
        migration.run()
        assert migration.db.execute("SELECT entity_id FROM memory_record_meta WHERE entity_id IS NOT NULL").fetchone()[0] == "existing-identity"
        assert migration.db.execute("SELECT entity_id FROM memory_entity_heads").fetchone()[0] == "existing-identity"
    finally:
        migration.close()


def test_seed_provenance_is_not_owner_testimony():
    from datetime import datetime
    from app.models.observation import Observation
    from app.services.observations import format_observation
    obs = Observation(id=9, origin="seed", observer="owner", content="Historical imported claim.",
                      level="explicit", subject="owner", domain="biography", created_at=datetime(2026, 9, 1),
                      reinforcement_count=1, sources=["Merged duplicate observation:8; original retained."])
    result = format_observation(obs)
    assert "origin:seed" in result and "legacy source unavailable" in result


def test_merge_does_not_resolve_numeric_conflict():
    rows=[dict(id=i,updated_at=str(i),content=f"# Project\n\n## Log\n- [2026-08-01] Measured {n} kg.\n") for i,n in [(1,40),(2,60)]]
    result=merge_documents(rows)
    assert "40 kg" in result and "60 kg" in result


def test_split_domain_monolith_leaves_active_tree_only_after_exact_preservation(snapshot, tmp_path):
    legacy = "# Wellness\n\n## Old profile\nUnique old detail not copied into the modern profile.\n"
    with sqlite3.connect(snapshot) as db:
        db.execute("INSERT INTO memory_files VALUES (6,1,'/memories/wellness.md',?,'2026-09-01')", (legacy,))
        db.execute("INSERT INTO memory_files VALUES (7,1,'/memories/wellness/09-26/profile.md','# Profile\nCurrent profile.','2026-09-28')")
        # A prior migration may already have a different edition at this path.
        db.execute("INSERT INTO memory_sources VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("a"*32,1,"prior-wellness","/memories/wellness.md","Older edition",
             digest("Older edition"),"historical","{}","earlier","2026-08-01"))
    migration = CorpusCleanup(snapshot, tmp_path / "retired.db")
    try:
        report = migration.run()
        assert report["source_unchanged"] and report["foreign_key_violations"] == 0
        assert not migration.db.execute("SELECT 1 FROM memory_files WHERE path='/memories/wellness.md'").fetchone()
        original = migration.db.execute("SELECT content FROM memory_sources WHERE original_path='/memories/wellness.md' AND content_hash=?", (digest(legacy),)).fetchone()
        assert original[0] == legacy
        assert migration.db.execute("SELECT 1 FROM memory_files WHERE path='/memories/wellness/09-26/profile.md'").fetchone()
        assert migration.db.execute("SELECT 1 FROM memory_issues WHERE kind='legacy_source_retired'").fetchone()
    finally:
        migration.close()


def test_unsplit_monolith_is_not_retired_without_a_replacement(snapshot, tmp_path):
    with sqlite3.connect(snapshot) as db:
        db.execute("INSERT INTO memory_files VALUES (6,1,'/memories/wellness.md','# Wellness\nOnly surviving information.','2026-09-01')")
    migration = CorpusCleanup(snapshot, tmp_path / "unsplit.db")
    try:
        migration.run()
        assert migration.db.execute("SELECT 1 FROM memory_files WHERE path='/memories/wellness.md'").fetchone()
    finally:
        migration.close()


def test_source_less_claim_is_labelled_without_changing_its_content():
    from datetime import datetime
    from app.models.observation import Observation
    from app.services.observations import format_observation
    obs=Observation(id=1,content="Measured 40 kg.",level="explicit",subject="owner",domain="wellness",
                    observer="atomix",created_at=datetime(2026,9,1),reinforcement_count=1)
    assert "legacy source unavailable" in format_observation(obs)
    assert obs.content=="Measured 40 kg."
    obs.message_ids=[42]
    assert "legacy source unavailable" not in format_observation(obs)


def test_large_reads_budget_headers_and_preserve_exact_offsets():
    from app.skills.memory import _page
    content="x"*12000+"\n"+"- detail\n"*1000
    path="/memories/general/09-26/large-source.md"
    result=_page(path,content,{"view_range":[1,2000]},budget=5500)
    assert len(result)<=5500 and "characters 0:" in result
    assert "Continue with char_range" in result
    numbered=_page(path,content,{"view_range":[2,2000]},budget=5500)
    assert len(numbered)<=5500 and "     2\t- detail" in numbered


async def test_agents_read_old_paths_sources_and_keep_catalog_current(snapshot,tmp_path,monkeypatch):
    output=tmp_path/"clean.db"
    migration=CorpusCleanup(snapshot,output)
    migration.run(); migration.close()
    engine=create_async_engine("sqlite+aiosqlite:///"+output.as_posix())
    sessions=async_sessionmaker(engine,expire_on_commit=False)
    async with sessions() as db:
        assert await resolve_alias(db,1,"/memories/social/personal/bedirhan.md")=="/memories/social/09-26/personal/bedirhan.md"
        assert await resolve_member(db,1,collection_by_root("/memories/social"),"bedirhan","personal")=="/memories/social/09-26/personal/bedirhan.md"
        context=SimpleNamespace(db=db,user_id=1)
        old=await MemorySkill().execute({"command":"view","path":"/memories/.archive/old-owner.md"},context)
        assert "Historical original" in old and "unique historical detail" in old
        assert "unique historical detail" in await MemorySkill().execute({"command":"search_sources","query":"unique historical"},context)
        assert "unique historical detail" not in await MemorySkill().execute({"command":"search_sources","query":"unique historical"},SimpleNamespace(db=db,user_id=2))
        recall=await recall_for_context(1,db,cache=MemoryRecallCache())
        assert "Never call a guess" in recall and len(recall)<6500
        file=(await db.execute(select(MemoryFile).where(MemoryFile.path.like('%bedirhan.md')))).scalar_one()
        meta=(await db.execute(select(MemoryRecordMeta).where(MemoryRecordMeta.memory_file_id==file.id))).scalar_one()
        record,version=meta.record_id,meta.version
        before=file.content
        await mutate_file(db,user_id=1,path=file.path,before=before,after=before+"\n- New owner detail.\n",author="owner",action="commit")
        await db.refresh(meta)
        from app.services.memory_identity import content_hash
        assert meta.record_id==record and meta.version==version+1
        assert meta.content_hash==content_hash(file.content)
    await engine.dispose()


async def test_finance_cutover_preserves_raw_history_without_double_counting(snapshot,tmp_path,monkeypatch):
    from app.services import finance_records as finance
    from app.skills.finance_record import FinanceRecordSkill
    from unittest.mock import AsyncMock
    record = {"id":"cinema","type":"transaction","description":"Cinema visit","status":"active",
              "date":"2026-09-28","movement":"expense","amount":"100.00","currency":"TRY",
              "account":"cash","event_ref":"cinema-receipt","evidence":[{"ref":"message:1","quote":"Cinema was 100 TRY."}]}
    with sqlite3.connect(snapshot) as db:
        db.execute("INSERT INTO sessions (id,user_id,agent_id,channel,triggered_by,model_used,started_at) VALUES (1,1,'sentinel','app','user','test','2026-09-28')")
        db.execute("INSERT INTO messages (id,session_id,role,content,created_at) VALUES (1,1,'user',?,'2026-09-28')",(json.dumps("Cinema was 100 TRY."),))
        db.execute("INSERT INTO memory_files VALUES (6,1,'/memories/finance/09-26/record-cinema.md',?,'2026-09-28')",(finance.encode(record),))
        db.execute("INSERT INTO memory_files VALUES (7,1,'/memories/finance/ledger/2026-09.md','# September\n\nOld expense total 9999 TRY.','2026-09-28')")
    output=tmp_path/"finance.db"
    migration=CorpusCleanup(snapshot,output)
    migration.run(); migration.close()
    reviewer=AsyncMock(return_value="Supported.")
    monkeypatch.setattr("app.services.memory_admission.admit",reviewer)
    engine=create_async_engine("sqlite+aiosqlite:///"+output.as_posix())
    async with async_sessionmaker(engine,expire_on_commit=False)() as db:
        ctx=SimpleNamespace(db=db,user_id=1,agent_id="sentinel",session_id=1,model="test",request_id="finance-migrated")
        skill=FinanceRecordSkill()
        summary=await skill.execute({"operation":"summary","month":"2026-09"},ctx)
        assert "100.00" in summary and "9999" not in summary
        legacy=(await db.execute(select(MemoryFile).where(MemoryFile.path=='/memories/finance/legacy/2026-09.md'))).scalar_one()
        assert "9999 TRY" in legacy.content
        current=json.loads(await skill.execute({"operation":"get","id":"cinema"},ctx))
        assert current["path"]=='/memories/finance/records/cinema.md'
        before=reviewer.await_count
        result=json.loads(await skill.execute({"operation":"put","version":current["version"],"record":record},ctx))
        assert result["written"]==current["path"] and reviewer.await_count==before
        record={**record,"description":"Cinema ticket"}
        assert "written" in await skill.execute({"operation":"put","version":current["version"],"record":record},ctx)
        docs=(await db.execute(select(MemoryFile).where(MemoryFile.path.startswith('/memories/finance/')))).scalars().all()
        from app.services.memory_identity import content_hash
        for file in docs:
            meta=(await db.execute(select(MemoryRecordMeta).where(MemoryRecordMeta.memory_file_id==file.id))).scalar_one()
            assert meta.content_hash==content_hash(file.content)
        assert len([file for file in docs if finance.MARKER in file.content])==1
    await engine.dispose()
