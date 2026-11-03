"""Read a consistent offline final-version SQLite snapshot into a NEW local DB.

No network, models, canonical rewrites, live connection or cutover. Copies original
document evidence and projects only supported non-derived current records.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys


def read_snapshot(path: Path, source_user_id: int) -> dict:
    path = path.resolve(strict=True)
    if any(Path(str(path) + suffix).exists() and Path(str(path) + suffix).stat().st_size
           for suffix in ("-wal", "-journal")):
        raise ValueError("Provide a consistent offline snapshot, without an active WAL/journal")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    uri = path.as_uri() + "?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("Snapshot integrity check failed")
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "memory_files" not in tables:
            raise ValueError("Expected a final-version memory_files table")
        rows = [dict(r) for r in db.execute("SELECT * FROM memory_files WHERE user_id=? ORDER BY id", (source_user_id,))]
        entities = {r["id"]: dict(r) for r in db.execute(
            "SELECT * FROM memory_entities WHERE user_id=?", (source_user_id,))} if "memory_entities" in tables else {}
        meta = {r["memory_file_id"]: dict(r) for r in db.execute(
            "SELECT * FROM memory_record_meta WHERE user_id=?", (source_user_id,))} if "memory_record_meta" in tables else {}
        heads = {r["entity_id"]: r["current_edition_id"] for r in db.execute(
            "SELECT * FROM memory_entity_heads WHERE user_id=?", (source_user_id,))} if "memory_entity_heads" in tables else {}
    after = hashlib.sha256(path.read_bytes()).hexdigest()
    if before != after:
        raise ValueError("Source snapshot changed while reading")
    return {"sha256": before, "path": str(path), "source_user_id": source_user_id, "files": rows,
            "entities": entities, "meta": meta, "heads": heads}


async def import_documents(snapshot, store, context):
    from sqlalchemy import select
    from app.models.memory_serving import MemoryServingUnit
    from app.services.memory_store import capture_source, exact_sections
    report = {"snapshot_sha256": snapshot["sha256"], "files": [], "scope": "document serving import only"}
    for row in snapshot["files"]:
        metadata = snapshot["meta"].get(row["id"], {})
        record_id = metadata.get("record_id") or f"legacy-file:{row['id']}"
        entity_id = metadata.get("entity_id")
        entity = snapshot["entities"].get(entity_id, {})
        aliases = entity.get("aliases") or []
        if isinstance(aliases, str):
            aliases = json.loads(aliases)
        if not isinstance(aliases, list) or any(not isinstance(a, str) for a in aliases):
            raise ValueError("Malformed source aliases require explicit reconciliation")
        title = entity.get("canonical_name") or next((line.lstrip("# ").strip()
            for line in (row["content"] or "").splitlines() if line.startswith("#")), "Memory record")
        # Path is a routing hint; stable record/entity IDs remain identity.
        path = row["path"].replace("\\", "/")
        derived = any(piece in path for piece in ("/states/", "/finance/", "/.views/")) or path.endswith(("/current.md", "/log.md"))
        archived = any(part.startswith(".archive") for part in path.split("/"))
        old_edition = archived or (entity_id in snapshot["heads"] and snapshot["heads"][entity_id] != record_id)
        eligible = not derived and not old_edition
        async with store.sessions() as db, db.begin():
            source = await capture_source(db, user_id=context.user_id,
                source_key=f"snapshot:{snapshot['sha256']}:file:{row['id']}",
                content=row["content"] or "", source_role="canonical",
                provenance={"original_path": path, "original_file_id": row["id"],
                    "record_id": record_id, "entity_id": entity_id,
                    "snapshot_sha256": snapshot["sha256"], "historical": old_edition,
                    "eligible_for_context": not derived and not archived,
                    "source_updated_at": row.get("updated_at"),
                    "currency": "unverified unless explicitly qualified"})
            spans = exact_sections(source)
            if eligible and spans:
                category = entity.get("entity_type")
                kind = category if category in {"person", "project", "course"} else "episode"
                if not category and "/social/" in path:
                    kind = "person"  # Existing collection grammar; not a new entity merge.
                if path.endswith("/owner.md"):
                    kind = "core"
                # A bounded view may have multiple sections/units, never a lost tail.
                for index in range(0, len(spans), 32):
                    key = f"record:{record_id}:part:{index // 32}"
                    old = (await db.execute(select(MemoryServingUnit).where(
                        MemoryServingUnit.user_id == context.user_id,
                        MemoryServingUnit.stable_key == key))).scalar_one_or_none()
                    await store.publish(context, stable_key=key, kind=kind, title=title[:256],
                        aliases=tuple(dict.fromkeys([entity["canonical_name"], *aliases])) if entity else (),
                        sections=spans[index:index+32], expected_revision=old.revision if old else 0, db=db)
            report["files"].append({"original_id": row["id"], "record_id": record_id,
                "source_id": source.id, "content_hash": source.content_hash,
                "disposition": "published" if eligible and spans else "preserved_source_only",
                "derived": derived, "historical": old_edition, "archived": archived})
    return report


async def import_history(snapshot, store, context):
    """Index originals without re-extracting them or replaying historical jobs."""
    from datetime import datetime
    from sqlalchemy.dialects.sqlite import insert
    from app.models.memory_serving import MemoryChange
    from app.services.memory_store import digest, fold, source_text
    counts = {"owner_messages": 0, "observation_claims": 0}
    with sqlite3.connect(Path(snapshot["path"]).as_uri()+"?mode=ro&immutable=1",uri=True) as source:
        source.row_factory = sqlite3.Row
        tables = {r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        batch = []
        async def flush():
            if batch:
                async with store.sessions() as db, db.begin():
                    await db.execute(insert(MemoryChange).on_conflict_do_nothing(
                        index_elements=["user_id","source_key"]), batch)
                batch.clear()
        previous = {}
        if {"messages","sessions"}.issubset(tables):
            for row in source.execute("SELECT m.*,s.triggered_by FROM messages m JOIN sessions s ON s.id=m.session_id WHERE s.user_id=? ORDER BY m.id",(snapshot["source_user_id"],)):
                try:
                    content = json.loads(row["content"]) if isinstance(row["content"],str) else row["content"]
                    body = source_text(content)
                except (ValueError,TypeError,AttributeError):
                    body = str(row["content"])
                prior = previous.get(row["session_id"])
                previous[row["session_id"]] = {"message_id":row["id"],"role":row["role"],
                    "text":body[-1200:],"fragment":len(body)>1200,
                    "authority":"context_only_not_owner_evidence"}
                if row["role"] != "user" or row["triggered_by"] != "user" or not body.strip():
                    continue
                batch.append({"user_id":context.user_id,
                    "source_key":f"snapshot:{snapshot['sha256']}:message:{row['id']}",
                    "source_role":"owner","content":body,"content_hash":digest(body),
                    "routing_text":fold(body+"\n"+(prior or {}).get("text","")),"outcome":"source_only",
                    "created_at":datetime.fromisoformat(row["created_at"]),
                    "provenance":{"snapshot_sha256":snapshot["sha256"],
                        "original_message_id":row["id"],"original_session_id":row["session_id"],
                        "prior_context":prior,"original_record_retained_in_snapshot":True}})
                counts["owner_messages"] += 1
                if len(batch)>=250:
                    await flush()
        if "observations" in tables:
            for row in source.execute("SELECT * FROM observations WHERE user_id=? ORDER BY id",(snapshot["source_user_id"],)):
                row = dict(row)
                body = row["content"] or ""
                eligible = row.get("level")=="explicit" and not row.get("deleted_at") and not row.get("superseded_by")
                batch.append({"user_id":context.user_id,
                    "source_key":f"snapshot:{snapshot['sha256']}:observation:{row['id']}",
                    "source_role":"quoted","content":body,"content_hash":digest(body),
                    "routing_text":fold(body),"outcome":"source_only",
                    "created_at":datetime.fromisoformat(row["created_at"]),
                    "provenance":{"snapshot_sha256":snapshot["sha256"],
                        "original_observation_id":row["id"],"observation_level":row.get("level"),
                        "valid_from":row.get("valid_from"),"valid_until":row.get("valid_until"),
                        "original_evidence_refs":row.get("sources"),
                        "original_record_retained_in_snapshot":True,"eligible_for_context":eligible}})
                counts["observation_claims"] += 1
                if len(batch)>=250:
                    await flush()
        await flush()
    return counts


async def run(args):
    destination = Path(args.destination).resolve()
    snapshot_path = Path(args.snapshot).resolve(strict=True)
    if destination == snapshot_path or destination.exists():
        raise ValueError("Destination must be a new, separate local database")
    report_path = Path(args.report).resolve()
    if report_path.exists() or report_path == snapshot_path or report_path == destination:
        raise ValueError("Report path must be new and separate from both databases")
    snapshot = read_snapshot(snapshot_path, args.user_id)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Reserve the name exclusively; never overwrite an existing user's dataset.
    destination.touch(exist_ok=False)
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + destination.as_posix()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.database import AsyncSessionLocal, close_db, init_db
    from app.core.context import AgentContext
    from app.models.user import User
    from app.services.memory_store import MemoryStore
    await init_db()
    try:
        async with AsyncSessionLocal() as db, db.begin():
            db.add(User(id=args.user_id, name="Owner", timezone=args.timezone))
        context = AgentContext(user_id=args.user_id, session_id=0, request_id="offline-import",
            triggered_by="user", trigger_payload={}, output_mode="silent", model="unused",
            system_prompt="", conversation_history=[], db=None, timezone=args.timezone)
        report = await import_documents(snapshot, MemoryStore(AsyncSessionLocal), context)
        report["history"] = await import_history(snapshot, MemoryStore(AsyncSessionLocal), context)
        from scripts.import_iacon_catalogue import import_catalogue
        report["iacon"] = await import_catalogue(snapshot, MemoryStore(AsyncSessionLocal), context)
        if hashlib.sha256(snapshot_path.read_bytes()).hexdigest() != snapshot["sha256"]:
            raise ValueError("Source changed during import; do not use this result")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with report_path.open("x", encoding="utf-8") as output:
            json.dump(report, output, indent=2)
    finally:
        await close_db()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--user-id", type=int, default=1)
    parser.add_argument("--timezone", default="Europe/Istanbul")
    asyncio.run(run(parser.parse_args()))
