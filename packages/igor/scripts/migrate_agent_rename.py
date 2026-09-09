# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
One-shot: rename an agent's id everywhere it is stored, so the history and the
settings a running deployment already accumulated under the old id follow the
rename — the database, AND runtime_state.json, whose maps are keyed by agent id
too.

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

The JSON half is the one that bites hardest, because it fails SILENTLY. An
orphaned `agent_models` entry does not error — the agent simply falls back to
its profile's own default model and keeps answering, on the wrong model, with
nothing in the logs saying so. Scourge ran on its profile's Anthropic default
for weeks that way while the Vertex pin the owner had set sat under `centurion`.

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
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text                              # noqa: E402

from app.database import AsyncSessionLocal, engine       # noqa: E402

# Runtime state is NOT in the database. app/core/runtime_state.py keeps a JSON
# file whose maps are keyed BY AGENT ID, and the rename orphans every one of
# them just as thoroughly as a table would be — worse, silently: an orphaned
# `agent_models` entry means the agent quietly falls back to its profile's own
# model, so it keeps answering, on the wrong model, with nothing in the logs
# saying so. That is exactly what happened to Scourge, which ran on its
# profile's Anthropic default for weeks while its Vertex pin sat under the old
# id.
#
# Dicts keyed by agent id, and lists holding agent ids. Enumerated rather than
# inferred: this file also holds OAuth tokens and chat ids, and a
# rewrite-anything-that-matches pass over that is not a thing to be clever with.
RUNTIME_STATE_DICTS = (
    "agent_models",       # the owner's model pin per agent
    "agent_sources",      # per-agent source-of-truth memory file
    "telegram_models",    # a second pin, for turns arriving over Telegram
    "voice_overrides",    # voice id + per-voice tuning
    "telegram_offsets",   # last-read update id per bot
)
RUNTIME_STATE_LISTS = (
    "telegram_started",   # which bots have been started
)


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


def _migrate_runtime_state(old: str, new: str, dry_run: bool) -> int:
    """Rewrite the agent id in runtime_state.json's agent-keyed maps.

    Same re-runnable shape as the SQL above: a second run matches nothing. Where
    BOTH ids are present (the app re-seeded the new one after the rename) the
    new entry is authoritative and the stale old one is dropped rather than
    overwriting it — the same rule agent_registry follows below.
    """
    from app.config import _DATA_DIR

    path = Path(_DATA_DIR) / "runtime_state.json"
    if not path.exists():
        print(f"  skip runtime_state.json (not at {path})")
        return 0

    state = json.loads(path.read_text(encoding="utf-8"))
    touched = 0

    for key in RUNTIME_STATE_DICTS:
        section = state.get(key)
        if not isinstance(section, dict) or old not in section:
            continue
        if new in section:
            print(f"  {'would drop' if dry_run else 'drop'}       1  "
                  f"runtime_state.{key}['{old}'] ('{new}' already set)")
        else:
            print(f"  {'would move' if dry_run else 'move'}       1  "
                  f"runtime_state.{key}['{old}'] -> ['{new}']")
            section[new] = section[old]
        del section[old]
        touched += 1

    for key in RUNTIME_STATE_LISTS:
        section = state.get(key)
        if not isinstance(section, list) or old not in section:
            continue
        rebuilt = [x for x in section if x != old]
        if new not in rebuilt:
            rebuilt.append(new)
        rebuilt.sort()
        print(f"  {'would fix' if dry_run else 'fix'}        1  runtime_state.{key}")
        state[key] = rebuilt
        touched += 1

    if touched and not dry_run:
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    if not touched:
        print("  runtime_state.json already clean")
    return touched


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

        total += _migrate_runtime_state(old, new, dry_run)

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
