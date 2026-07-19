"""
One-shot migration: copy SPEDA's existing SQLite database into Postgres,
preserving everything — sessions, messages, memory files (including the indexed
history facts), users, tool calls. Run this ONCE when moving to the server so you
never re-run (or re-pay for) history indexing.

Usage:
    # from packages/igor, with the venv active
    python scripts/migrate_sqlite_to_postgres.py \
        --source ~/.speda/speda.db \
        --dest "postgresql+asyncpg://speda:speda@localhost:5432/speda"

If --dest is omitted, DATABASE_URL from the environment/.env is used.
If --source is omitted, the default ~/.speda/speda.db is used.

Safe to inspect first: pass --dry-run to print row counts without writing.
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Ensure THIS project's `app` package wins, regardless of cwd or any other
# SPEDA checkout on the machine (scripts/ on sys.path[0] would otherwise let a
# stale sibling project's `app` shadow it).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import create_async_engine

# Importing the package registers every model's table on Base.metadata.
import app.models  # noqa: F401
from app.database import Base


def _normalise_sqlite_url(source: str) -> str:
    p = Path(source).expanduser()
    return f"sqlite+aiosqlite:///{p}"


async def migrate(source: str, dest: str, dry_run: bool) -> None:
    src_engine = create_async_engine(_normalise_sqlite_url(source))
    dst_engine = create_async_engine(dest)

    tables = list(Base.metadata.sorted_tables)  # FK-safe insertion order

    # 1. Ensure the destination schema exists.
    if not dry_run:
        async with dst_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    # 2. Copy each table.
    async with src_engine.connect() as src:
        for table in tables:
            rows = [dict(r) for r in (await src.execute(select(table))).mappings().all()]
            print(f"{table.name:<16} {len(rows):>6} rows", end="")

            if dry_run or not rows:
                print(" (skipped)" if dry_run else " (empty)")
                continue

            async with dst_engine.begin() as dst:
                # SQLite does not enforce foreign keys by default, so the source
                # can hold orphaned rows (e.g. embeddings whose message was
                # deleted). Postgres enforces FKs, which would reject them. Defer
                # FK checks for this transaction so the copy is faithful; orphans
                # load harmlessly. Requires superuser (the postgres-image role is).
                if dest.startswith("postgresql"):
                    await dst.execute(text("SET LOCAL session_replication_role = replica"))
                # Insert in chunks to stay well under parameter limits.
                CHUNK = 500
                for i in range(0, len(rows), CHUNK):
                    await dst.execute(insert(table), rows[i:i + CHUNK])
            print("  -> copied")

    # 3. Reset Postgres auto-increment sequences so future inserts don't collide
    #    with the IDs we just copied verbatim.
    if not dry_run and dest.startswith("postgresql"):
        async with dst_engine.begin() as dst:
            for table in tables:
                pk = [c.name for c in table.primary_key.columns]
                if pk == ["id"]:
                    await dst.execute(text(
                        f"SELECT setval(pg_get_serial_sequence('{table.name}', 'id'), "
                        f"COALESCE((SELECT MAX(id) FROM {table.name}), 1), true)"
                    ))
        print("sequences reset")

    await src_engine.dispose()
    await dst_engine.dispose()
    print("\nDone." if not dry_run else "\nDry run complete — nothing written.")


def main() -> None:
    from app.config import settings

    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=str(Path.home() / ".speda" / "speda.db"))
    ap.add_argument("--dest", default=settings.database_url)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.dry_run and not args.dest.startswith("postgresql"):
        print(f"Refusing to run: --dest is not Postgres ({args.dest}).")
        print("Pass --dest 'postgresql+asyncpg://speda:speda@HOST:5432/speda'")
        return

    print(f"source: {args.source}")
    print(f"dest:   {args.dest}\n")
    asyncio.run(migrate(args.source, args.dest, args.dry_run))


if __name__ == "__main__":
    main()
