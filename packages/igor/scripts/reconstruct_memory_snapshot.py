"""Lossless offline reconciliation using the implemented memory contracts.

Never loads the app lifespan, drains jobs, calls a provider, or opens a live DB.
Outputs include all-row dispositions and original changed rows outside the repo.
Reviewed corrections are fingerprint-bound to the supplied October 10 snapshot.
"""
import argparse
import asyncio
import base64
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import shutil

from app.services.memory_cleanup import CorpusCleanup, digest, file_hash, packed, stable_id

SOURCE_SHA = "55c560e0ca3dff13f79d390a514fa54dbf447b768597c6f0efc170b5410a1de7"
FIELDS = ("content", "level", "subject", "domain", "valid_from", "valid_until",
          "source_ids", "premises", "sources", "pattern_type", "confidence")
JSON_FIELDS = {"source_ids", "premises", "sources", "message_ids"}


def unpack_observation(row):
    result = dict(row)
    for field in JSON_FIELDS:
        result[field] = json.loads(result[field] or "[]")
    return result


def message_text(content):
    value = json.loads(content)
    if isinstance(value, str):
        return value
    return "\n".join(b.get("text", "") for b in value if isinstance(b, dict) and b.get("type") == "text") if isinstance(value, list) else ""


def decoded_quote(value):
    # Historical sources sometimes double-escaped Unicode. Never evaluate text.
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m[1], 16)), value).replace("\\n", "\n")


def reconcile(cleanup, reviewed_source=None):
    from app.services.observations import validate_observation, ObservationRejected, fragment_reason
    db = cleanup.db
    now = cleanup.now
    decisions = {}
    owner_messages = {}
    for row in db.execute("SELECT m.id,m.content,s.id session_id FROM messages m JOIN sessions s ON s.id=m.session_id WHERE m.role='user' AND s.triggered_by='user' AND s.user_id=1"):
        owner_messages[row["id"]] = (message_text(row["content"]), row["session_id"])
    reviewed_corrections = cleanup.fingerprint == SOURCE_SHA
    if reviewed_source is not None:
        if file_hash(reviewed_source) != SOURCE_SHA:
            raise ValueError("Reviewed source fingerprint is not the approved original")
        # Reuse reviewed interpretations only when the underlying claims and
        # literal evidence are unchanged in the fresh live source. New live
        # provenance/reinforcement is retained by the normal update operations.
        with closing(sqlite3.connect(reviewed_source.resolve().as_uri()+"?mode=ro&immutable=1", uri=True)) as reviewed:
            reviewed.row_factory = sqlite3.Row
            fields = ("content","level","subject","domain","valid_from","valid_until",
                      "superseded_by","deleted_at","origin","source_ids","premises",
                      "pattern_type","confidence")
            for ident in (851,852,856,2125,692,2440,2391,2441,2442,2108,2110,2413):
                before = reviewed.execute("SELECT * FROM observations WHERE id=?", (ident,)).fetchone()
                live = cleanup.original.execute("SELECT * FROM observations WHERE id=?", (ident,)).fetchone()
                if not before or not live or any(before[f] != live[f] for f in fields):
                    raise ValueError(f"Reviewed observation {ident} changed; fresh evidence review required")
            for ident in (24717,24965,24413,24977,21536,21542):
                before = reviewed.execute("SELECT role,content,session_id FROM messages WHERE id=?", (ident,)).fetchone()
                live = cleanup.original.execute("SELECT role,content,session_id FROM messages WHERE id=?", (ident,)).fetchone()
                if not before or not live or tuple(before) != tuple(live):
                    raise ValueError(f"Reviewed message {ident} changed; fresh evidence review required")
            for ident in (2108,2110):
                before = reviewed.execute("SELECT * FROM pattern_evidence WHERE evidence_ref=?", (f"observation:{ident}",)).fetchone()
                live = cleanup.original.execute("SELECT * FROM pattern_evidence WHERE evidence_ref=?", (f"observation:{ident}",)).fetchone()
                if before and (not live or tuple(before) != tuple(live)):
                    raise ValueError(f"Reviewed pattern evidence {ident} changed")
        reviewed_corrections = True
    def update(ident, changes, reason, evidence=()):
        old = db.execute("SELECT * FROM observations WHERE id=?", (ident,)).fetchone()
        cleanup.snapshot("observations", old)
        changes["updated_at"] = now
        values = [json.dumps(v, ensure_ascii=False) if k in JSON_FIELDS else v for k,v in changes.items()]
        db.execute("UPDATE observations SET " + ",".join(f'"{k}"=?' for k in changes) + " WHERE id=?", (*values, ident))
        decisions.setdefault(str(ident), []).append({"reason": reason, "changes": list(changes), "evidence": list(evidence)})
        cleanup.issue(old["user_id"], f"reconstruction:{ident}:{len(decisions[str(ident)])}", "reconstruction_decision", [f"observation:{ident}", *evidence], reason)

    with db:
        # Strict, literal provenance recovery; a quote of assistant reply context
        # is not an authenticated owner assertion, even when in a user message.
        for original in list(db.execute("SELECT * FROM observations ORDER BY id")):
            obs = unpack_observation(original)
            if obs["deleted_at"]:
                continue
            verified = []
            for source in obs["sources"]:
                match = re.match(r"^message:(\d+):\s*(.*)$", str(source), re.S)
                if not match:
                    continue
                mid, quote = int(match[1]), decoded_quote(match[2]).strip()
                text = owner_messages.get(mid, ("", None))[0]
                asserted = text.split("\n\n", 1)[-1] if text.startswith("Reply context (selected chat excerpts):") else text
                if quote and len(quote) >= 12 and quote in asserted:
                    verified.append(mid)
            ids = sorted(set(obs["message_ids"] + verified))
            if ids != obs["message_ids"]:
                update(obs["id"], {"message_ids": ids}, "Recovered literal owner-message citation; import origin retained.", [f"message:{i}" for i in verified])
            if obs["origin"] in {"seed", "reindex"} and not verified and not obs["message_ids"]:
                # Source documents are evidence of the imported wording, not of
                # direct owner speech. Limit exact matches, retaining all originals.
                sources = list(obs["sources"])
                for capsule in db.execute("SELECT id,content FROM memory_sources WHERE classification IN ('original','historical') AND length(content)<500000 ORDER BY source_key"):
                    if obs["content"] in capsule["content"]:
                        sources.append(f"source:{capsule['id']}: {obs['content']}")
                        break
                if sources != obs["sources"]:
                    update(obs["id"], {"sources": sources}, "Linked exact imported wording to preserved document; no owner-testimony promotion.")

        if reviewed_corrections:
            # Human-reviewed corrections use only these exact owner messages.
            assert "All work related stuff with ostim IT is over" in owner_messages[24717][0]
            assert "I REVOKED SINAN KARA NICKNAME RULE" in owner_messages[24965][0]
            assert "Yok yok öyle bir samimiyetimiz yok" in owner_messages[24413][0]
            for ident in (851, 852, 856, 2125):
                update(ident, {"valid_until": "2026-10-07", "superseded_by": 2413},
                       "Closed prior OSTIM IT employment/supervision state at explicit owner termination; historical wording preserved.", ("message:24717", "observation:2413"))
            update(692, {"valid_until": "2026-10-09", "superseded_by": 2440},
                   "Ended exclusive nickname preference at explicit revocation; the historical naming event remains intact.", ("message:24965", "observation:2440"))
            update(2440, {"valid_from": "2026-10-09"}, "Resolved effective date from explicit owner revocation message.", ("message:24965",))
            update(2391, {"deleted_at": now}, "Quarantined unsupported invented project/familiarity claim; owner said there was no such familiarity. Original and source remain available.", ("message:24413",))
            for ident in (2441, 2442):
                update(ident, {"deleted_at": now}, "Quarantined assistant reply-context excerpt misattributed as owner-stated fact. Conversation and original observation preserved.", ("message:24977",))
            # Existing pattern's two premises are directly supported in the same
            # session; preserve one independent source group, not two events.
            for ident, mid in ((2108, 21536), (2110, 21542)):
                obs = unpack_observation(db.execute("SELECT * FROM observations WHERE id=?", (ident,)).fetchone())
                update(ident, {"message_ids": sorted(set(obs["message_ids"] + [mid])),
                              "sources": obs["sources"] + [f"message:{mid}: {owner_messages[mid][0]}"]},
                       "Recovered pattern premise from original owner session; both premises remain one independent session.", (f"message:{mid}",))
                evidence = db.execute("SELECT * FROM pattern_evidence WHERE evidence_ref=?", (f"observation:{ident}",)).fetchone()
                if evidence:
                    cleanup.snapshot("pattern_evidence", evidence)
                    db.execute("UPDATE pattern_evidence SET locator=?,source_hash=? WHERE id=?", (json.dumps({"message_id": mid, "session_id":1497, "quote":owner_messages[mid][0]}),digest(owner_messages[mid][0]), evidence["id"]))

        # Mechanical form corrections only. Temporal reversals and ambiguous
        # fragments are quarantined rather than assigned invented dates/verbs.
        for original in list(db.execute("SELECT * FROM observations WHERE deleted_at IS NULL ORDER BY id")):
            obs = unpack_observation(original)
            changes = {}
            if obs["domain"] == "biography" and obs["valid_until"]:
                changes["domain"] = "state"
                obs["domain"] = "state"
            reason = fragment_reason(obs["content"])
            if reason and "never says who" in reason:
                subj = obs["subject"]
                label = "owner" if subj == "owner" else subj.replace(":", " ", 1)
                changes["content"] = obs["content"] = f"The {label}: {obs['content']}"
            try:
                validate_observation(**{k:obs[k] for k in FIELDS})
            except ObservationRejected as exc:
                update(obs["id"], {"deleted_at": now}, "Quarantined form/chronology ambiguity under current validator: " + str(exc))
            else:
                if changes:
                    update(obs["id"], changes, "Mechanical classification/context correction using existing subject and validity; no new claim inferred.")

        # Preserve searchable historical document editions. Raw revisions are
        # unchanged; editions sharing identical bytes remain separately traced.
        existing = {(r[0], r[1]) for r in db.execute("SELECT original_path,content_hash FROM memory_sources")}
        added = 0
        for rev in cleanup.original.execute("SELECT * FROM memory_revisions ORDER BY id"):
            for side in ("before", "after"):
                text = rev[side]
                if not text or not rev["path"].startswith("/memories/") or (rev["path"],digest(text)) in existing:
                    continue
                key = f"{cleanup.fingerprint}:revision:{rev['id']}:{side}"
                classification = "system" if "/." in rev["path"] else "historical"
                db.execute("INSERT INTO memory_sources VALUES (?,?,?,?,?,?,?,?,?,?)",(stable_id(key),rev["user_id"],key,rev["path"],text,digest(text),classification,packed(rev),cleanup.migration_id,now))
                existing.add((rev["path"],digest(text)))
                added += 1
    reconstruction_indexes=rebuild_lexical(cleanup)
    return {"observation_decisions":decisions,"historical_revision_editions_added":added,"lexical_rebuild":reconstruction_indexes,
            "reviewed_corrections_applied":reviewed_corrections,
            "reviewed_baseline_sha256":SOURCE_SHA if reviewed_corrections else None}


def rebuild_lexical(cleanup):
    """Re-project current text, preserving orphan index-only source material."""
    from app.services.lexical import fold
    from app.services.embedding_indexer import _extract_text
    db=cleanup.db
    missing=db.execute("SELECT count(*) FROM messages m LEFT JOIN messages_fts f ON f.rowid=m.id WHERE f.rowid IS NULL").fetchone()[0]
    orphans=list(cleanup.original.execute("SELECT f.rowid original_rowid,f.text FROM messages_fts f LEFT JOIN messages m ON m.id=f.rowid WHERE m.id IS NULL"))
    with db:
        for row in orphans:
            key=f"{cleanup.fingerprint}:messagefts:{row['original_rowid']}"
            db.execute("INSERT OR IGNORE INTO memory_sources VALUES (?,?,?,?,?,?,?,?,?,?)",(stable_id(key),1,key,f"message:{row['original_rowid']}",row["text"],digest(row["text"]),"orphan_message",packed(row),cleanup.migration_id,cleanup.now))
            cleanup.issue(1,f"orphan-message-index:{row['original_rowid']}","orphan_message_index",["source:"+stable_id(key)],"Only a folded search-index text survives; original message and speaker cannot be verified. Preserved as historical source, never reconstructed as an owner conversation.")
        db.execute("DELETE FROM observations_fts")
        db.executemany("INSERT INTO observations_fts(rowid,content,subject,domain,observer) VALUES (?,?,?,?,?)",[(r["id"],*(fold(r[k]) for k in ("content","subject","domain","observer"))) for r in db.execute("SELECT * FROM observations WHERE deleted_at IS NULL")])
        db.execute("DELETE FROM messages_fts")
        db.executemany("INSERT INTO messages_fts(rowid,text) VALUES (?,?)",[(r["id"],fold(_extract_text(json.loads(r["content"])))) for r in db.execute("SELECT id,content FROM messages")])
    return {"previously_unindexed_messages":missing,"orphan_index_texts_preserved":len(orphans),"messages_indexed":db.execute("SELECT count(*) FROM messages_fts").fetchone()[0],"active_observations_indexed":db.execute("SELECT count(*) FROM observations_fts").fetchone()[0]}


async def repair_passages(output):
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.models.memory_file import MemoryFile
    from app.models.memory_record_meta import MemoryRecordMeta
    from app.models.memory_passage import MemoryPassage
    from app.services.memory_passages import index_passages
    engine = create_async_engine("sqlite+aiosqlite:///" + output.as_posix())
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        live = (await db.execute(select(MemoryFile,MemoryRecordMeta).join(MemoryRecordMeta,MemoryRecordMeta.memory_file_id==MemoryFile.id))).all()
        ids = {m.record_id for _,m in live}
        for passage in (await db.execute(select(MemoryPassage))).scalars():
            if passage.record_id not in ids:
                passage.retired = True
        await db.flush()
        for file,meta in live:
            await index_passages(db,file.user_id,meta.record_id,file.path,file.content)
            await db.flush()
        await db.commit()
    await engine.dispose()


def reconcile_graph(cleanup):
    """Keep historical references resolvable; quarantine truly dangling edges."""
    db = cleanup.db
    references = {}
    for kind, table, col in (("message","messages","id"),("observation","observations","id"),
                            ("revision","memory_revisions","id"),("source","memory_sources","id"),
                            ("passage","memory_passages","id"),("entity","memory_entities","id")):
        references[kind] = {str(r[0]) for r in db.execute(f'SELECT "{col}" FROM "{table}"')}
    references["record"] = {r[0] for r in db.execute("SELECT record_id FROM memory_record_meta")}
    references["record"].update(r[0] for r in db.execute("SELECT record_id FROM memory_revisions WHERE record_id IS NOT NULL"))
    references["record"].update(r[0] for r in cleanup.original.execute("SELECT record_id FROM memory_record_meta"))
    references["path"] = {r[0] for r in db.execute("SELECT path FROM memory_files UNION SELECT old_path FROM memory_path_aliases UNION SELECT original_path FROM memory_sources")}
    def valid(ref):
        if not ref:
            return True
        kind,_,ident = ref.partition(":")
        if kind == "entity" and (ident == "owner" or ident.startswith(("person:","project:"))):
            return True  # Existing graph intentionally has virtual subject nodes.
        return ident in references.get(kind,set())
    removed = []
    with db:
        for row in list(db.execute("SELECT * FROM memory_graph_edges")):
            wrapped = re.fullmatch(r"path:observation:(\d+)", row["target_ref"])
            if row["relation_type"] == "changed" and wrapped and row["source_ref"].startswith("revision:"):
                revision = db.execute("SELECT path FROM memory_revisions WHERE id=?", (int(row["source_ref"].split(":",1)[1]),)).fetchone()
                if revision and revision[0] == row["target_ref"][5:] and wrapped[1] in references["observation"]:
                    cleanup.snapshot("memory_graph_edges",row)
                    target = "observation:"+wrapped[1]
                    db.execute("UPDATE memory_graph_edges SET target_ref=? WHERE id=?", (target,row["id"]))
                    cleanup.issue(row["user_id"],f"graph-native-ref:{row['id']}","graph_reference_repaired",[row["source_ref"],target],"Literal revision path and existing fact ID prove malformed path wrapper; native observation reference restored. Original edge preserved.")
                    row = db.execute("SELECT * FROM memory_graph_edges WHERE id=?", (row["id"],)).fetchone()
            broken = [row[k] for k in ("source_ref","target_ref","evidence_ref") if not valid(row[k])]
            if not broken:
                continue
            cleanup.snapshot("memory_graph_edges",row)
            cleanup.issue(row["user_id"],f"graph-quarantine:{row['id']}","dangling_graph_edge",broken,"Unresolvable historical graph reference. Full original edge retained in cleanup snapshot and all-row disposition archive.")
            db.execute("DELETE FROM memory_graph_edges WHERE id=?",(row["id"],))
            removed.append({"id":row["id"],"broken_refs":broken})
        # Supplement precisely recovered evidence and temporal relations.
        for row in db.execute("SELECT id,user_id,message_ids,superseded_by FROM observations"):
            for mid in json.loads(row["message_ids"] or "[]"):
                if str(mid) in references["message"]:
                    cleanup.edge(row["user_id"],f"observation:{row['id']}",f"message:{mid}","evidenced_by",f"message:{mid}")
            if row["superseded_by"]:
                cleanup.edge(row["user_id"],f"observation:{row['id']}",f"observation:{row['superseded_by']}","superseded_by")
    return removed


def table_rows(db,table):
    info = list(db.execute(f'PRAGMA table_info("{table}")'))
    pk = [r[1] for r in sorted(info,key=lambda r:r[5]) if r[5]]
    order = ','.join('"'+c+'"' for c in pk) if pk else 'rowid'
    for ordinal,row in enumerate(db.execute(f'SELECT * FROM "{table}" ORDER BY {order}')):
        yield row, {c:row[c] for c in pk} if pk else {"ordinal":ordinal}


def account(cleanup, artifacts, decisions):
    counts = Counter()
    totals = {}
    changed_tables = []
    original, dst = cleanup.original,cleanup.db
    from app.services.memory_cleanup import build_plan
    canonical_keepers={row["id"]:unit["keeper"] for unit in build_plan([dict(r) for r in original.execute("SELECT * FROM memory_files")]) for row in unit["rows"]}
    tables = [r[0] for r in original.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    with (artifacts/"record-dispositions.jsonl").open("w",encoding="utf-8") as mapping, (artifacts/"changed-original-records.jsonl").open("w",encoding="utf-8") as changes:
        for table in tables:
            unchanged = CorpusCleanup.table_hash(original,table) == CorpusCleanup.table_hash(dst,table)
            if not unchanged:
                changed_tables.append(table)
            info = list(original.execute(f'PRAGMA table_info("{table}")'))
            pk = [r[1] for r in sorted(info,key=lambda r:r[5]) if r[5]]
            num = 0
            for row,key in table_rows(original,table):
                payload = packed(row)
                sha = digest(payload)
                disposition, targets = "preserved", [{"table":table,"key":key}]
                if not unchanged:
                    current = dst.execute(f'SELECT * FROM "{table}" WHERE '+" AND ".join(f'"{c}"=?' for c in pk),tuple(row[c] for c in pk)).fetchone() if pk else None
                    if current is None or packed(current) != payload:
                        disposition = "transformed" if current else "quarantined"
                        if table.startswith(("observations_fts","messages_fts")):
                            disposition, targets = "transformed", [{"table":table,"derived_index":True}]
                        elif table == "memory_files":
                            capsule = dst.execute("SELECT id FROM memory_sources WHERE source_key=?",(f"{cleanup.fingerprint}:file:{row['id']}",)).fetchone()
                            targets = [{"table":"memory_sources","key":{"id":capsule[0]}}]
                            if current:
                                targets.append({"table":table,"key":key})
                            elif row["id"] in canonical_keepers:
                                targets.append({"table":table,"key":{"id":canonical_keepers[row["id"]]}})
                            disposition = "transformed" if current else "merged" if "/." not in row["path"] else "retained_as_historical_evidence"
                        elif table == "observations":
                            disposition = "quarantined" if current and current["deleted_at"] and not row["deleted_at"] else "superseded" if current and current["superseded_by"] != row["superseded_by"] else "transformed"
                        elif not current:
                            targets = [{"artifact":"changed-original-records.jsonl","sha256":sha}]
                            if table == "message_embeddings":
                                capsule=dst.execute("SELECT id FROM memory_sources WHERE source_key=?",(f"{cleanup.fingerprint}:embedding:{row['id']}",)).fetchone()
                                if capsule:
                                    targets.append({"table":"memory_sources","key":{"id":capsule[0]}})
                            elif table == "memory_record_meta":
                                target=dst.execute("SELECT m.id FROM memory_record_links l JOIN memory_record_meta m ON m.record_id=l.target_record_id AND m.user_id=l.user_id WHERE l.source_record_id=? AND l.user_id=?",(row["record_id"],row["user_id"])).fetchone()
                                if target:
                                    targets.append({"table":table,"key":{"id":target[0]}})
                        changes.write(json.dumps({"table":table,"key":key,"sha256":sha,"original":json.loads(payload)},ensure_ascii=False,default=lambda x:{"base64":base64.b64encode(x).decode()})+"\n")
                entry = {"source_table":table,"source_key":key,"source_sha256":sha,"disposition":disposition,"destinations":targets}
                if table == "observations" and str(row["id"]) in decisions:
                    entry["decision_refs"] = [f"reconstruction:{row['id']}:{i+1}" for i in range(len(decisions[str(row['id'])]))]
                mapping.write(json.dumps(entry,ensure_ascii=False,default=lambda x:{"base64":base64.b64encode(x).decode()})+"\n")
                counts[disposition] += 1
                num += 1
            totals[table] = num
    return {"source_record_count":sum(totals.values()),"mapped_record_count":sum(counts.values()),"disposition_counts":dict(counts),"table_counts":totals,"changed_tables":changed_tables}


def validate_database(cleanup):
    import app.models
    from app.database import Base
    from app.services.observations import validate_observation
    from app.services.memory_passages import passages
    from app.services.memory_verify import verify_document
    from app.services.memory_states import parse as parse_state
    from app.services.finance_records import MARKER, parse as parse_finance
    from app.services.lexical import fold
    from app.services.embedding_indexer import _extract_text
    db=cleanup.db
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert not list(db.execute("PRAGMA foreign_key_check"))
    for row in db.execute("SELECT m.id,m.content,f.text FROM messages m LEFT JOIN messages_fts f ON f.rowid=m.id"):
        assert row["text"]==fold(_extract_text(json.loads(row["content"]))),(row["id"],"message FTS projection")
    assert db.execute("SELECT count(*) FROM messages_fts").fetchone()[0]==db.execute("SELECT count(*) FROM messages").fetchone()[0]
    for row in db.execute("SELECT o.*,f.content indexed_content,f.subject indexed_subject,f.domain indexed_domain,f.observer indexed_observer FROM observations o LEFT JOIN observations_fts f ON f.rowid=o.id WHERE o.deleted_at IS NULL"):
        assert all(row["indexed_"+k]==fold(row[k]) for k in ("content","subject","domain","observer")),(row["id"],"observation FTS projection")
    assert db.execute("SELECT count(*) FROM observations_fts").fetchone()[0]==db.execute("SELECT count(*) FROM observations WHERE deleted_at IS NULL").fetchone()[0]
    for name,table in Base.metadata.tables.items():
        columns={r[1] for r in db.execute(f'PRAGMA table_info("{name}")')}
        assert all(col.name in columns for col in table.columns),(name,"current ORM columns")
    for table in ("messages","sessions","message_embeddings","memory_revisions"):
        if table == "message_embeddings":
            continue  # Original orphan rows are preserved as capsules.
        limit = cleanup.original.execute("SELECT max(id) FROM "+table).fetchone()[0]
        assert CorpusCleanup.table_hash(cleanup.original,table) == CorpusCleanup.table_hash(db,table,before_id=limit),table
    assert not db.execute("SELECT 1 FROM memory_entity_heads h LEFT JOIN memory_entities e ON e.id=h.entity_id LEFT JOIN memory_record_meta m ON m.record_id=h.current_edition_id WHERE e.id IS NULL OR m.id IS NULL OR m.entity_id<>h.entity_id").fetchone()
    assert not db.execute("SELECT 1 FROM memory_path_aliases a LEFT JOIN memory_record_meta m ON m.record_id=a.target_record_id WHERE a.target_record_id IS NOT NULL AND m.id IS NULL").fetchone()
    for row in db.execute("SELECT * FROM observations WHERE deleted_at IS NULL"):
        obs=unpack_observation(row)
        validate_observation(**{k:obs[k] for k in FIELDS})
        for ident in obs["source_ids"]:
            assert db.execute("SELECT 1 FROM observations WHERE id=? AND user_id=?",(ident,row["user_id"])).fetchone()
        if row["superseded_by"]:
            assert db.execute("SELECT 1 FROM observations WHERE id=? AND user_id=?",(row["superseded_by"],row["user_id"])).fetchone()
    active_passages=0
    for row in db.execute("SELECT f.*,m.record_id,m.content_hash FROM memory_files f JOIN memory_record_meta m ON m.memory_file_id=f.id"):
        assert row["content_hash"]==digest(row["content"])
        assert not verify_document(row["path"],row["content"])
        if row["path"].startswith("/memories/states/"):
            parse_state(row["content"])
        if MARKER in row["content"]:
            parse_finance(row["content"])
        expected={p["id"]:p for p in reversed(list(passages(row["record_id"],row["content"])))}
        actual={p["id"]:p for p in db.execute("SELECT * FROM memory_passages WHERE record_id=? AND retired=0",(row["record_id"],))}
        assert set(expected)==set(actual),(row["path"],"passage IDs")
        for ident,p in expected.items():
            assert actual[ident]["text"]==p["text"] and actual[ident]["start_line"]==p["start_line"] and actual[ident]["path"]==row["path"]
        active_passages+=len(actual)
    # Every removed original document remains byte-for-byte in source storage.
    for row in cleanup.original.execute("SELECT * FROM memory_files"):
        capsule=db.execute("SELECT original_row,content FROM memory_sources WHERE source_key=?",(f"{cleanup.fingerprint}:file:{row['id']}",)).fetchone()
        assert capsule and capsule[0]==packed(row) and capsule[1]==row["content"]
    vectors=0
    for row in cleanup.original.execute("SELECT id,embedding FROM observations WHERE embedding IS NOT NULL"):
        assert db.execute("SELECT embedding FROM observations WHERE id=?",(row["id"],)).fetchone()[0]==row["embedding"]
        vectors+=1
    return {"sqlite_integrity":"ok","foreign_key_violations":0,"source_unchanged":file_hash(cleanup.source)==cleanup.fingerprint,
            "raw_conversations_unchanged":True,"original_revision_prefix_unchanged":True,"active_observations_valid":db.execute("SELECT count(*) FROM observations WHERE deleted_at IS NULL").fetchone()[0],
            "all_original_observation_vectors_preserved":vectors,"active_passages_verified":active_passages,"dangling_catalog_heads":0,"dangling_aliases":0,"all_current_ORM_tables_and_columns_present":True,"complete_keyword_index_projections_verified":True}


async def smoke(output,artifacts):
    """Real app APIs, stored-vector hybrid queries; write on disposable copy."""
    from types import SimpleNamespace
    import numpy as np
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
    from app.models.observation import Observation
    from app.models.memory_file import MemoryFile
    from app.models.memory_record_meta import MemoryRecordMeta
    from app.services.observations import search_observations
    from app.skills.memory import MemorySkill, relevant_files_for_message
    from app.skills.patterns import InspectPatternsSkill
    from app.services.memory_store import mutate_file
    from app.services.memory_graph import build_context
    scratch=artifacts/"write-validation.db"
    if scratch.exists():
        raise ValueError("Validation scratch already exists; choose fresh artifact directory.")
    with closing(sqlite3.connect(output)) as source,closing(sqlite3.connect(scratch)) as target:
        source.backup(target)
    engine=create_async_engine("sqlite+aiosqlite:///"+scratch.as_posix())
    results=[]
    async with async_sessionmaker(engine,expire_on_commit=False)() as db:
        context=SimpleNamespace(db=db,user_id=1,agent_id="speda",session_id=1,request_id="offline-validation",timezone="Europe/Istanbul",extra={})
        for category,query in (("personal","Bursa"),("academic","WAP"),("project","Blackwalnut"),("relationship","Sinan"),("temporal","OSTİM")):
            lexical=await search_observations(db,user_id=1,query=query,lexical_only=True,limit=10)
            assert lexical,(category,"lexical recall")
            blobs=dict((await db.execute(select(Observation.id,Observation.embedding).where(Observation.id.in_([o.id for o,_ in lexical])))).all())
            probe=next((o for o,_ in lexical if blobs.get(o.id)),None)
            assert probe,(category,"stored vector")
            hybrid=await search_observations(db,user_id=1,query=query,prepared_query=(query,query,np.frombuffer(blobs[probe.id],dtype=np.float32)),limit=10)
            assert any(o.id==probe.id for o,_ in hybrid),(category,"hybrid self-recall")
            document_query={"personal":"Bursa summer employment","academic":"MIS215","project":"Blackwalnut","relationship":"Sinan","temporal":"ostim"}[category]
            document=await relevant_files_for_message(1,db,"What about " + document_query + "?")
            assert document,(category,"native document retrieval")
            results.append({"category":category,"query":query,"lexical_ids":[o.id for o,_ in lexical],"hybrid_probe_id":probe.id,"hybrid_ids":[o.id for o,_ in hybrid],"document_output_chars":len(document)})
        old=await search_observations(db,user_id=1,query="OSTİM",lexical_only=True,as_of="2026-10-06",limit=100)
        current=await search_observations(db,user_id=1,query="OSTİM",lexical_only=True,as_of="2026-10-08",limit=100)
        assert 851 in {o.id for o,_ in old} and 851 not in {o.id for o,_ in current}
        pattern=await InspectPatternsSkill().execute({"mode":"evidence","pattern_id":2111},context)
        assert json.loads(pattern)[0]["independent_sources"]==1
        assert "21536" in pattern and "21542" in pattern
        graph=await build_context(db,1,"observation:2413",depth=2)
        assert graph and "24717" in graph
        # Preserve old path lookup and archived-source retrieval through real tools.
        doc=await MemorySkill().execute({"command":"view","path":"/memories/projects/blackwalnut/09-26.md"},context)
        assert "Blackwalnut" in doc
        sources=await MemorySkill().execute({"command":"search_sources","query":"Blackwalnut"},context)
        assert sources and "nothing" not in sources.lower()
        from sqlalchemy import text
        from app.skills.semantic_search import SemanticSearchSkill
        source_message=(await db.execute(text("SELECT message_id,text,embedding FROM message_embeddings WHERE message_id=24717"))).first()
        assert source_message
        history=await SemanticSearchSkill().execute({"query":"OSTIM IT work", "limit":5,
                    "_prepared_query":("OSTIM IT work","OSTIM IT work",np.frombuffer(source_message.embedding,dtype=np.float32))},context)
        assert "message:24717" in history
        from app.skills.finance_record import FinanceRecordSkill
        summary=await FinanceRecordSkill().execute({"operation":"summary","month":"2026-09"},context)
        assert summary and "Error" not in summary
        file,meta=(await db.execute(select(MemoryFile,MemoryRecordMeta).join(MemoryRecordMeta,MemoryRecordMeta.memory_file_id==MemoryFile.id).where(MemoryFile.path.like('%blackwalnut.md')))).first()
        before=file.content
        ident,version=meta.record_id,meta.version
        # Literal owner write, no semantic reviewer/network; disposable only.
        await mutate_file(db,user_id=1,path=file.path,before=before,after=before+"\n- Offline validation marker.\n",author="owner",action="commit")
        await db.refresh(meta)
        assert meta.record_id==ident and meta.version==version+1 and meta.content_hash==digest(file.content)
        try:
            await mutate_file(db,user_id=1,path=file.path,before=before,after=before+"\n- Stale write.\n",author="owner",action="commit")
        except Exception as exc:
            assert "changed" in str(exc).lower() or "conflict" in str(exc).lower() or "stale" in str(exc).lower(),str(exc)
        else:
            raise AssertionError("Stale compare-and-swap write was accepted")
    await engine.dispose()
    return {"representative_recall":results,"temporal_before_after":True,"pattern_premises_verified":True,"graph_evidence_recall":True,"legacy_alias_and_historical_source_recall":True,"transactional_write_and_stale_write_rejection":True,
            "conversation_semantic_recall_verified":True,"typed_finance_summary_verified":True,"fresh_query_embedding_provider_tested":False,"semantic_test_method":"Existing stored-vector queries through current hybrid fact/conversation retrievers; no remote embedding call."}


def rehearse_cutover(output,original,artifacts):
    # Same-volume atomic replace + exact-file rollback, exclusively in staging.
    live=artifacts/"cutover-rehearsal-active.db"
    rollback=artifacts/"cutover-rehearsal-rollback.db"
    candidate=artifacts/"cutover-rehearsal-candidate.db"
    for path in (live,rollback,candidate):
        if path.exists():
            raise ValueError("Rehearsal path already exists")
    shutil.copy2(original,live)
    shutil.copy2(live,rollback)
    shutil.copy2(output,candidate)
    candidate.replace(live)
    assert file_hash(live)==file_hash(output)
    with closing(sqlite3.connect(live)) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
    rollback.replace(live)
    assert file_hash(live)==file_hash(original)
    return {"atomic_same_volume_swap_tested":True,"byte_identical_rollback_tested":True,"production_opened_or_modified":False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--artifacts",type=Path,required=True)
    parser.add_argument("--resume-validation",action="store_true",help="Resume checks on an already reconstructed offline output; do not repeat transformations.")
    parser.add_argument("--reviewed-source",type=Path,help="Approved original baseline; reuse reviewed corrections only after checking unchanged claims and raw evidence against the fresh source.")
    args=parser.parse_args()
    source,output,artifacts=args.source.resolve(),args.output.resolve(),args.artifacts.resolve()
    if output.exists() and not args.resume_validation:
        raise ValueError("Fresh output required; preserve previous runs.")
    if source.parent != output.parent or artifacts == source.parent:
        raise ValueError("Output must be alongside offline source; artifacts must be a separate fresh directory.")
    artifacts.mkdir(parents=True,exist_ok=args.resume_validation)
    cleanup=CorpusCleanup(source,output)
    try:
        if args.resume_validation:
            baseline=json.loads(cleanup.db.execute("SELECT reconciliation_report FROM memory_migration_runs WHERE plan_id=?",(cleanup.migration_id,)).fetchone()[0])
            reconstruction=json.loads((artifacts/"reconstruction-decisions.json").read_text(encoding="utf-8"))
            quarantined_edges=json.loads((artifacts/"graph-quarantine.json").read_text(encoding="utf-8"))
        else:
            baseline=cleanup.run()
            print("Native cleanup completed",flush=True)
            reconstruction=reconcile(cleanup,args.reviewed_source)
            (artifacts/"reconstruction-decisions.json").write_text(json.dumps(reconstruction,ensure_ascii=False,indent=2),encoding="utf-8")
            print("Observation/provenance reconciliation completed",flush=True)
            cleanup.db.close()
            asyncio.run(repair_passages(output))
            cleanup.db=sqlite3.connect(output);cleanup.db.row_factory=sqlite3.Row
            cleanup.db.execute("PRAGMA foreign_keys=ON")
            quarantined_edges=reconcile_graph(cleanup)
            (artifacts/"graph-quarantine.json").write_text(json.dumps(quarantined_edges,indent=2),encoding="utf-8")
        validation=validate_database(cleanup)
        accounting=account(cleanup,artifacts,reconstruction["observation_decisions"])
        (artifacts/"database-validation.json").write_text(json.dumps(validation,indent=2),encoding="utf-8")
        (artifacts/"record-accounting.json").write_text(json.dumps(accounting,indent=2),encoding="utf-8")
        assert accounting["source_record_count"]==accounting["mapped_record_count"]
        print("Database/accounting validation completed",flush=True)
        cleanup.db.close()
        attempt=1
        while (artifacts/f"validation-attempt-{attempt}").exists():
            attempt+=1
        checks_dir=artifacts/f"validation-attempt-{attempt}"
        checks_dir.mkdir()
        api_checks=asyncio.run(smoke(output,checks_dir))
        (artifacts/"api-validation.json").write_text(json.dumps(api_checks,indent=2),encoding="utf-8")
        rehearsal=rehearse_cutover(output,source,checks_dir)
        report={"source_sha256":cleanup.fingerprint,"output_sha256":file_hash(output),"baseline_cleanup":baseline,"reconstruction":reconstruction,"graph_edges_quarantined":quarantined_edges,"validation":validation,"record_accounting":accounting,"api_validation":api_checks,"cutover_rehearsal":rehearsal,"production_cutover":"NOT PERFORMED; requires owner approval and quiesced service"}
        (artifacts/"validation-report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        manifest={p.name:{"sha256":file_hash(p),"bytes":p.stat().st_size} for p in artifacts.iterdir() if p.is_file()}
        (artifacts/"artifact-checksums.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
        print(json.dumps({"output":str(output),"report":str(artifacts/"validation-report.json"),"validation":validation,"dispositions":accounting["disposition_counts"]},indent=2))
    finally:
        cleanup.close()


if __name__=="__main__":
    main()
