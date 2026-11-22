"""Idempotent historical backfill CLI for Operation Iacon (links -> bi-temporal edges, aliases, narratives).

Preserves all immutable MemoryChange rows and revision history. Safe to re-run.
Usage:
  PYTHONDONTWRITEBYTECODE=1 python scripts/backfill_operation_iacon.py [--user-id 1] [--use-llm]
"""
from __future__ import annotations

import argparse
import asyncio
import json

from app.database import Base, async_session, engine
from app.services.iacon_catalogue import Catalogue
from app.services.iacon_dreaming import OrionDreamer
from app.services.memory_store import MemoryStore


async def run_backfill(user_id: int, *, use_llm: bool = False, force: bool = False) -> dict:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    store = MemoryStore(async_session)
    catalogue = Catalogue(store)
    dreamer = OrionDreamer(catalogue)
    bf = await catalogue.backfill_links_and_aliases(user_id)
    dream = await dreamer.dream_user(user_id, use_llm=use_llm, force=force)
    return {"backfill": bf, "dream": dream}


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill Operation Iacon edges, aliases, and narratives.")
    parser.add_argument("--user-id", type=int, default=1)
    parser.add_argument("--use-llm", action="store_true", help="Use configured MaintenanceBudget LLM if available")
    parser.add_argument("--force", action="store_true", help="Force narrative regeneration even if digest unchanged")
    args = parser.parse_args()
    result = asyncio.run(run_backfill(args.user_id, use_llm=args.use_llm, force=args.force))
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
