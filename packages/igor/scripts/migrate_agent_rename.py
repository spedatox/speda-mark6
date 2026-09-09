# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
One-shot: rename an agent's id in every table that stores it, so the history a
running deployment already accumulated under the old id follows the rename.

    python scripts/migrate_agent_rename.py --dry-run          # count, touch nothing
    python scripts/migrate_agent_rename.py                     # centurion -> scourge
    python scripts/migrate_agent_rename.py --from X --to Y     # any other rename

WHY THIS EXISTS. An agent id is not only a code constant — it is the key rows
are written under: a chat session, a project, a reminder, a memory revision, a
message embedding, a peer message, an observation, an automation and the agent's
own registry row all carry it. Rename the id in the source alone and every one
of those rows is orphaned: old Scourge chats, reminders and projects go
invisible because the code now queries `scourge` while the rows still say
`centurion`. This rewrites them in place.

Every column below holds an agent id (verified against app/models on the day of
the centurion -> scourge rename). `from_agent`/`to_agent` on agent_messages and
`observer` on observations are agent ids too, not just the obvious `agent_id`;
`author` (memory_revisions) and `created_by` (news_watches) hold `owner` OR an
agent id, so they are matched by value, not blindly rewritten.

SAFE TO RE-RUN. Every UPDATE is `SET col = :to WHERE col = :from`; a second run
matches nothing. agent_registry.agent_id is a primary key — if a row already
exists under the new id (e.g. the app booted and re-seeded it after the rename),
the old row's UPDATE would collide, so that one table is handled last and the
stale old-id row is deleted instead of updated when the new row is already there.
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text                              # noqa: E402

from app.database import AsyncSessionLocal, engine       # noqa: E402

# (table, column). Ordinary agent-id columns rewritten unconditionally for the
# matched value. agent_registry is deliberately absent here — its primary key
# needs the collision handling below.
COLUMNS: list[tuple[str, str]] = [
    ("automations", "agent_id"),
    ("sessions", "agent_id"),
    ("projects", "agent_id"),
    ("reminder_definitions", "agent_id"),
    ("reminder_cycles", "agent_id"),
    ("message_embeddings", "agent_id"),
    ("memory_revisions", "author"),
    ("news_watches", "created_by"),
    ("observations", "observer"),
    ("agent_messages", "from_agent"),
    ("agent_messages", "to_agent"),
]


async def _count(session, table: str, col: str, value: str) -> int:
    row = await session.execute(
        text(f"SELECT COUNT(*) FROM {table} WHERE {col} = :v"), {"v": value}
    )
    return int(row.scalar() or 0)


async def _table_exists(session, table: str) -> bool:
    # Works on both SQLite and Postgres via the SQLAlchemy inspector.
    def _has(conn):
        from sqlalchemy import inspect

        return table in inspect(conn).get_table_names()

    return await session.run_sync(lambda s: _has(s.connection()))


async def run(old: str, new: str, dry_run: bool) -> None:
    async with AsyncSessionLocal() as session:
        total = 0
        for table, col in COLUMNS:
            if not await _table_exists(session, table):
                print(f"  skip {table}.{col} (table absent)")
                continue
            n = await _count(session, table, col, old)
            total += n
            verb = "would update" if dry_run else "update"
            print(f"  {verb} {n:>5}  {table}.{col}")
            if n and not dry_run:
                await session.execute(
                    text(f"UPDATE {table} SET {col} = :new WHERE {col} = :old"),
                    {"new": new, "old": old},
                )

        # agent_registry.agent_id is the primary key — handle the collision.
        if await _table_exists(session, "agent_registry"):
            old_row = await _count(session, "agent_registry", "agent_id", old)
            new_row = await _count(session, "agent_registry", "agent_id", new)
            if old_row and new_row:
                print(f"  {'would delete' if dry_run else 'delete'}     1  "
                      f"agent_registry stale '{old}' row ('{new}' already present)")
                if not dry_run:
                    await session.execute(
                        text("DELETE FROM agent_registry WHERE agent_id = :old"),
                        {"old": old},
                    )
            elif old_row:
                print(f"  {'would update' if dry_run else 'update'}     1  "
                      f"agent_registry.agent_id")
                if not dry_run:
                    await session.execute(
                        text("UPDATE agent_registry SET agent_id = :new "
                             "WHERE agent_id = :old"),
                        {"new": new, "old": old},
                    )
            total += old_row

        if dry_run:
            print(f"\nDRY RUN — {total} rows would change '{old}' -> '{new}'. "
                  "Re-run without --dry-run to apply.")
        else:
            await session.commit()
            print(f"\nDone — {total} rows moved '{old}' -> '{new}'.")

    await engine.dispose()


def main() -> None:
    ap = argparse.ArgumentParser(description="Rename an agent id across the DB.")
    ap.add_argument("--from", dest="old", default="centurion")
    ap.add_argument("--to", dest="new", default="scourge")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    print(f"Agent rename '{args.old}' -> '{args.new}'"
          f"{'  (dry run)' if args.dry_run else ''}")
    asyncio.run(run(args.old, args.new, args.dry_run))


if __name__ == "__main__":
    main()
