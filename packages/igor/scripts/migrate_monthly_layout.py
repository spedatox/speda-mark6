# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Migration runner for the Monthly Memory Architecture (§11).

Discovers legacy memory documents, generates a deterministic content-addressed
migration plan, runs rehearsal validation, and executes cutover atomically.
Every moved or split original survives under `.archive/` and in `memory_revisions`.
Paths are aliased in `memory_path_aliases` for transparent read redirect.

Usage:
  python -m scripts.migrate_monthly_layout --inventory
  python -m scripts.migrate_monthly_layout --generate-plan plan.json
  python -m scripts.migrate_monthly_layout --dry-run plan.json
  python -m scripts.migrate_monthly_layout --apply plan.json
"""

import argparse
import asyncio
import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, update, delete
from app.database import AsyncSessionLocal
from app.models.memory_file import MemoryFile
from app.models.memory_path_alias import MemoryPathAlias
from app.models.memory_record_meta import MemoryRecordMeta
from app.models.memory_migration_run import MemoryMigrationRun
from app.services.memory_store import record_revision
from app.services.memory_paths import MonthPeriod, slugify
from app.services.memory_calendar import month_for_event, TemporalResolution, PeriodBasis, DatePrecision
from app.core.clock import owner_today, owner_now

logger = logging.getLogger(__name__)


def file_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


async def run_inventory(db, user_id: int = 1) -> dict:
    """Phase A: Discover all memory rows and categorize by migration need."""
    stmt = select(MemoryFile).where(MemoryFile.user_id == user_id).order_by(MemoryFile.path)
    res = await db.execute(stmt)
    files = res.scalars().all()

    inventory = {
        "total_files": len(files),
        "life_files": [],
        "events_files": [],
        "finance_ledger_files": [],
        "states_files": [],
        "other_files": [],
    }

    for f in files:
        if f.path.startswith("/memories/.archive/") or f.path.startswith("/memories/.audit/"):
            continue
        if f.path.startswith("/memories/life/"):
            inventory["life_files"].append(f.path)
        elif f.path.startswith("/memories/events/"):
            inventory["events_files"].append(f.path)
        elif f.path.startswith("/memories/finance/ledger/"):
            inventory["finance_ledger_files"].append(f.path)
        elif f.path.startswith("/memories/states/"):
            inventory["states_files"].append(f.path)
        else:
            inventory["other_files"].append(f.path)

    return inventory


async def generate_plan(db, user_id: int = 1, plan_id: str = "") -> dict:
    """Phase B: Generate a deterministic content-addressed migration plan."""
    if not plan_id:
        plan_id = f"monthly-migration-{owner_today().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"

    stmt = select(MemoryFile).where(MemoryFile.user_id == user_id).order_by(MemoryFile.path)
    res = await db.execute(stmt)
    files = res.scalars().all()

    today = owner_today()
    default_period = MonthPeriod.from_date(today).folder_name
    canonical_period = MonthPeriod.from_date(today).canonical

    moves = []
    aliases = []

    for f in files:
        if f.path.startswith("/memories/.archive/") or f.path.startswith("/memories/.audit/"):
            continue

        # 1. /memories/life/<slug>.md -> /memories/general/<MM-YY>/<slug>.md
        if f.path.startswith("/memories/life/"):
            slug = f.path.removeprefix("/memories/life/").removesuffix(".md")
            new_path = f"/memories/general/{default_period}/{slug}.md"
            moves.append({
                "action": "move",
                "source_path": f.path,
                "target_path": new_path,
                "expected_sha256": file_sha256(f.content),
                "content": f.content,
                "category": "general",
                "period": canonical_period,
            })
            aliases.append({
                "old_path": f.path,
                "target_path": new_path,
                "alias_type": "moved"
            })

        # 2. /memories/events/<YYYY-MM>.md -> /memories/general/<MM-YY>/events.md
        elif f.path.startswith("/memories/events/"):
            m = re.search(r"(\d{4})-(\d{2})\.md$", f.path)
            if m:
                yyyy, mm = m.group(1), m.group(2)
                period_folder = f"{mm}-{yyyy[-2:]}"
                canonical_p = f"{yyyy}-{mm}"
            else:
                period_folder = default_period
                canonical_p = canonical_period

            new_path = f"/memories/general/{period_folder}/events.md"
            moves.append({
                "action": "move",
                "source_path": f.path,
                "target_path": new_path,
                "expected_sha256": file_sha256(f.content),
                "content": f.content,
                "category": "general",
                "period": canonical_p,
            })
            aliases.append({
                "old_path": f.path,
                "target_path": new_path,
                "alias_type": "moved"
            })

    plan = {
        "id": plan_id,
        "user_id": user_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "total_moves": len(moves),
        "moves": moves,
        "aliases": aliases,
    }
    plan["plan_hash"] = hashlib.sha256(json.dumps(plan, sort_keys=True).encode("utf-8")).hexdigest()
    return plan


async def apply_plan(db, plan: dict, *, apply: bool = False) -> dict:
    """Phase C & D: Rehearsal validation and cutover execution."""
    plan_id = plan["id"]
    user_id = plan["user_id"]
    moves = plan.get("moves", [])
    aliases = plan.get("aliases", [])

    report = {
        "plan_id": plan_id,
        "valid": True,
        "errors": [],
        "applied": False,
        "migrated_count": 0,
    }

    # Validation pass (Rehearsal)
    for m in moves:
        src = m["source_path"]
        expected_hash = m["expected_sha256"]
        row = (await db.execute(select(MemoryFile).where(
            MemoryFile.user_id == user_id, MemoryFile.path == src
        ))).scalar_one_or_none()

        if not row:
            report["valid"] = False
            report["errors"].append(f"Source file missing: {src}")
            continue

        actual_hash = file_sha256(row.content)
        if actual_hash != expected_hash:
            report["valid"] = False
            report["errors"].append(f"Content hash mismatch at {src}: expected {expected_hash}, got {actual_hash}")

    if not report["valid"] or not apply:
        return report

    # Cutover pass inside transaction
    migration_run = MemoryMigrationRun(
        plan_id=plan_id,
        plan_hash=plan.get("plan_hash", ""),
        source_fingerprint=file_sha256(str(len(moves))),
        phase="cutover",
        schema_version_from=1,
        schema_version_to=2,
        total_sources=len(moves),
        committed_count=0,
    )
    db.add(migration_run)

    for m in moves:
        src = m["source_path"]
        dst = m["target_path"]
        content = m["content"]

        # 1. Archive original
        archive_path = f"/memories/.archive/{plan_id}/{src.removeprefix('/memories/')}"
        db.add(MemoryFile(user_id=user_id, path=archive_path, content=content))
        await record_revision(db, user_id=user_id, path=archive_path, author="migration",
                              action="archive", before="", after=content, request_id=plan_id)

        # 2. Delete source
        await db.execute(delete(MemoryFile).where(MemoryFile.user_id == user_id, MemoryFile.path == src))
        await record_revision(db, user_id=user_id, path=src, author="migration",
                              action="delete", before=content, after="", request_id=plan_id)

        # 3. Create target
        new_file = MemoryFile(user_id=user_id, path=dst, content=content)
        db.add(new_file)
        await db.flush()

        # 4. Metadata
        record_id = uuid.uuid4().hex
        meta = MemoryRecordMeta(
            memory_file_id=new_file.id,
            record_id=record_id,
            user_id=user_id,
            category=m.get("category", "general"),
            kind="event",
            period=m.get("period", ""),
            period_basis="legacy_recorded",
            recorded_at=datetime.now(timezone.utc),
            date_precision="unknown",
            schema_version=2,
            content_hash=file_sha256(content),
        )
        db.add(meta)

        await record_revision(db, user_id=user_id, path=dst, author="migration",
                              action="create", before="", after=content, request_id=plan_id,
                              record_id=record_id, migration_id=plan_id)

        report["migrated_count"] += 1

    # Add path aliases
    for a in aliases:
        old_path = a["old_path"]
        existing_alias = (await db.execute(select(MemoryPathAlias).where(
            MemoryPathAlias.user_id == user_id, MemoryPathAlias.old_path == old_path
        ))).scalar_one_or_none()

        if not existing_alias:
            db.add(MemoryPathAlias(
                user_id=user_id,
                old_path=old_path,
                alias_type=a["alias_type"],
                migration_id=plan_id,
            ))

    migration_run.phase = "completed"
    migration_run.committed_count = report["migrated_count"]
    migration_run.commit_watermark = datetime.now(timezone.utc)
    report["applied"] = True

    await db.commit()
    return report


async def main():
    parser = argparse.ArgumentParser(description="Monthly memory layout migration tool")
    parser.add_argument("--inventory", action="store_true", help="Inspect memory files")
    parser.add_argument("--generate-plan", type=str, metavar="OUT_FILE", help="Generate migration plan to JSON")
    parser.add_argument("--dry-run", type=str, metavar="PLAN_FILE", help="Validate migration plan")
    parser.add_argument("--apply", type=str, metavar="PLAN_FILE", help="Apply migration plan")
    parser.add_argument("--user-id", type=int, default=1, help="User ID (default: 1)")

    args = parser.parse_args()

    async with AsyncSessionLocal() as db:
        if args.inventory:
            inv = await run_inventory(db, args.user_id)
            print(json.dumps(inv, indent=2))
        elif args.generate_plan:
            plan = await generate_plan(db, args.user_id)
            Path(args.generate_plan).write_text(json.dumps(plan, indent=2), encoding="utf-8")
            print(f"Generated plan {plan['id']} with {plan['total_moves']} moves to {args.generate_plan}")
        elif args.dry_run:
            plan = json.loads(Path(args.dry_run).read_text(encoding="utf-8"))
            report = await apply_plan(db, plan, apply=False)
            print(json.dumps(report, indent=2))
        elif args.apply:
            plan = json.loads(Path(args.apply).read_text(encoding="utf-8"))
            report = await apply_plan(db, plan, apply=True)
            print(json.dumps(report, indent=2))
        else:
            parser.print_help()


if __name__ == "__main__":
    asyncio.run(main())
