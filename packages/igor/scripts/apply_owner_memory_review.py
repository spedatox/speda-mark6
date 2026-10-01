# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Apply an explicit private reviewed plan to a separate offline SQLite copy."""

import argparse
import asyncio
import json
import sqlite3
from pathlib import Path

from sqlalchemy import select
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models
from app.models.memory_migration_run import MemoryMigrationRun
from app.services.memory_cleanup import CorpusCleanup, file_hash
from app.services.memory_owner_review import apply_owner_unit, stable_hash


async def run(source: Path, output: Path, plan: dict) -> dict:
    source, output = source.resolve(), output.resolve()
    if source == output or (output.exists() and source.samefile(output)):
        raise ValueError("In-place owner review is forbidden; keep the immutable input.")
    for path in (source, output):
        if Path(str(path)+"-wal").exists():
            raise ValueError("Review requires a consistent offline snapshot, not a WAL database.")
    fingerprint = file_hash(source)
    plan_hash = stable_hash(plan)
    plan_id = "owner-review:" + plan_hash[:40]
    if not output.exists():
        original = sqlite3.connect(source.as_uri()+"?mode=ro", uri=True)
        target = sqlite3.connect(output)
        try:
            original.backup(target)
        finally:
            original.close(); target.close()
    engine = create_async_engine("sqlite+aiosqlite:///"+output.as_posix())
    @event.listens_for(engine.sync_engine, "connect")
    def configure_connection(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=15000")
        cursor.close()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as db:
            checkpoint = (await db.execute(select(MemoryMigrationRun).where(
                MemoryMigrationRun.plan_id == plan_id,
            ))).scalar_one_or_none()
            if checkpoint is None:
                # Refuse an existing unrelated target, even if one document matches.
                # SQLite backup can legitimately change physical page bytes.
                # Compare complete table contents and schema instead of file
                # hashes, while the immutable input retains its byte hash.
                with sqlite3.connect(source.as_uri()+"?mode=ro", uri=True) as original, sqlite3.connect(output.as_uri()+"?mode=ro", uri=True) as target:
                    original.row_factory = target.row_factory = sqlite3.Row
                    schema = lambda connection: list(connection.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name"))
                    if [tuple(row) for row in schema(original)] != [tuple(row) for row in schema(target)]:
                        raise ValueError("Output schema belongs to a different source.")
                    for row in schema(original):
                        if CorpusCleanup.table_hash(original, row["name"]) != CorpusCleanup.table_hash(target, row["name"]):
                            raise ValueError("Output belongs to a different source or reviewed plan.")
                checkpoint = MemoryMigrationRun(plan_id=plan_id, plan_hash=plan_hash,
                    source_fingerprint=fingerprint, phase="owner_review",
                    schema_version_from=3, schema_version_to=3, total_sources=len(plan["units"]))
                db.add(checkpoint)
                await db.commit()
            elif checkpoint.source_fingerprint != fingerprint or checkpoint.plan_hash != plan_hash:
                raise ValueError("Source/plan changed; resume only the original reviewed plan.")
            results = []
            for unit in plan["units"]:
                results.append(await apply_owner_unit(db, user_id=plan["user_id"],
                    unit=unit, sources=plan["sources"], plan_id=plan_id))
            checkpoint.committed_count = len(results)
            checkpoint.phase = "completed"
            await db.commit()
        if file_hash(source) != fingerprint:
            raise ValueError("Immutable source changed during review.")
        with sqlite3.connect(output) as check:
            integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_keys = list(check.execute("PRAGMA foreign_key_check"))
        if integrity != "ok" or foreign_keys:
            raise ValueError("Output failed SQLite integrity/foreign key validation.")
        return {"source_unchanged": True, "source_sha256": fingerprint,
                "plan_id": plan_id, "units": results, "provider_calls": 0,
                "integrity": integrity, "foreign_key_violations": len(foreign_keys)}
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--export", type=Path)
    args = parser.parse_args()
    report = asyncio.run(run(args.source, args.output, json.loads(args.plan.read_text(encoding="utf-8"))))
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.export:
        from scripts.cleanup_memory_corpus import export_corpus
        export_corpus(args.output, args.export)
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
