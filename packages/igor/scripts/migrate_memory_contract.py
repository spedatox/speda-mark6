# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Apply an operator-reviewed, content-addressed memory repair plan atomically.

Run from packages/igor with the app interpreter. Default is dry-run. Plans hold
private owner content and MUST stay outside git. Back up SQLite with its backup
API before --apply. Every replaced original also survives under .archive/ and
in MemoryRevision. A concurrent edit aborts the entire plan; never force it.

Plan: {"id":"...", "user_id":1, "changes":[{"path":"/memories/...",
"expected_sha256": "..." or null for absent, "content":"..."}]}.
Reapplying an already-applied plan is a no-op. Rollback is a new reviewed plan
with the post-migration hashes and contents from the preserved originals.
"""

import argparse
import asyncio
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, update
from app.database import AsyncSessionLocal
from app.models.memory_file import MemoryFile
from app.services.memory_store import record_revision
from app.services.memory_states import parse, version
from app.services.memory_verify import introduced_by


async def apply_plan(db, plan: dict, *, apply: bool = False) -> dict:
    plan_id = plan["id"]
    if not re.fullmatch(r"[a-z0-9-]+", plan_id):
        raise ValueError("Plan id must be lowercase-hyphenated.")
    user_id = int(plan["user_id"])
    seen, pending = set(), []
    for change in plan["changes"]:
        path, after = change["path"], change["content"]
        if path in seen or not path.startswith("/memories/") or ".." in path or "\\" in path:
            raise ValueError(f"Duplicate/invalid path: {path}")
        seen.add(path)
        row = (await db.execute(select(MemoryFile).where(
            MemoryFile.user_id == user_id, MemoryFile.path == path,
        ).execution_options(populate_existing=True))).scalar_one_or_none()
        before = row.content if row else None
        if before == after:
            continue
        if (version(before) if before is not None else None) != change["expected_sha256"]:
            raise ValueError(f"Plan is stale at {path}; no changes applied.")
        if path.startswith("/memories/states/"):
            record = parse(after)
            if path != f"/memories/states/{record['key']}.md":
                raise ValueError("State identity mismatch.")
        if path == "/memories/current.md" and "<!-- state-projection-v1 -->" not in after:
            raise ValueError("Current migration must enable the state projection.")
        errors = [f.message for f in introduced_by(path, before or "", after) if f.severity == "error"]
        if errors:
            raise ValueError(f"Invalid planned document {path}: {errors}")
        pending.append((path, before, after))
    report = {"plan": plan_id, "pending": [p for p, _, _ in pending], "applied": False}
    if not apply or not pending:
        return report
    try:
        for path, before, after in pending:
            if before is not None:
                archive = f"/memories/.archive/{plan_id}/{path.removeprefix('/memories/')}"
                old = (await db.execute(select(MemoryFile).where(
                    MemoryFile.user_id == user_id, MemoryFile.path == archive))).scalar_one_or_none()
                if old is not None and old.content != before:
                    raise ValueError(f"Archive collision at {archive}")
                if old is None:
                    db.add(MemoryFile(user_id=user_id, path=archive, content=before))
                    await record_revision(db, user_id=user_id, path=archive, author="migration",
                                          action="archive", before="", after=before, request_id=plan_id)
                result = await db.execute(update(MemoryFile).where(
                    MemoryFile.user_id == user_id, MemoryFile.path == path,
                    MemoryFile.content == before,
                ).values(content=after, updated_at=datetime.now(timezone.utc))
                  .execution_options(synchronize_session="fetch"))
                if result.rowcount != 1:
                    raise ValueError(f"Concurrent write at {path}; entire plan rolled back.")
            else:
                db.add(MemoryFile(user_id=user_id, path=path, content=after))
            await record_revision(db, user_id=user_id, path=path, author="migration",
                                  action="memory_contract", before=before or "", after=after,
                                  request_id=plan_id)
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    report["applied"] = True
    return report


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8-sig"))
    async with AsyncSessionLocal() as db:
        print(json.dumps(await apply_plan(db, plan, apply=args.apply)))


if __name__ == "__main__":
    asyncio.run(main())
