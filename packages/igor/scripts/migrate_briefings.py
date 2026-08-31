# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
One-shot: move the scheduled briefings out of the `Scheduled briefings` n8n
workflow and into the automation module, where the owner can edit them from
Heartbreaker.

    python scripts/migrate_briefings.py --dry-run   # show the plan, touch nothing
    python scripts/migrate_briefings.py             # do it

WHAT IT REPLACES. `scripts/n8n/daily_briefings.json` held every briefing as one
hand-edited JavaScript array inside a Code node, ticking every five minutes and
deciding "not yet" ~287 times a day. That design predates the automation module
and has three costs the module does not have: the briefings are invisible in the
UI, changing one means editing JS and re-importing a workflow, and a briefing
fires up to five minutes late because a tick is the only clock. After this runs,
each briefing is its own n8n workflow on its own real cron, and the row in the
`automations` table is what the owner edits.

WHAT IS PRESERVED, EXACTLY. The intents are the hand-tuned ones — several of
them written in response to the 2026-08-05 incident where a stale live copy sent
a briefing in the format its committed version already banned
(services/n8n_drift.py). They are carried over verbatim from
`scripts/briefings_seed.json` and are marked `polished` so the intent polisher
never touches them: an automatic rewrite of that text is exactly the accident
this script must not cause.

The FACTS block that told Atomix whether today is a gym day is preserved too,
as the `day_flags` spec field — the composer emits the same computation as its
own Code node (see composer._day_flags_code). What is NOT carried over is the
five-minute tick, the `store.fired` once-a-day latch and the 15-minute grace
window: all three exist only to make a ticking workflow behave like a cron, and
each briefing now has a real one.

NOT MIGRATED: `gym_renewal` (a one-off dated 2026-07-29 — already fired and
long past) and the commented-out `monthly_finance`.

ORDER, AND WHY. Every automation is created and verified FIRST; the old
workflow is deactivated only once they all exist. The reverse order would leave
a window with no briefings at all if a create failed, whereas this order's worst
case is a few seconds where both could fire. Re-running is safe: the manager
refuses to stack a second active automation with the same name, so a second run
reports what already exists and changes nothing.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.automations import manager                      # noqa: E402
from app.database import AsyncSessionLocal               # noqa: E402
from app.services.n8n_api import N8nClient               # noqa: E402

SEED = Path(__file__).resolve().parent / "briefings_seed.json"

# The workflow this replaces, by the name n8n knows it under.
LEGACY_WORKFLOW = "Scheduled briefings"


def _load() -> list[dict]:
    definitions = json.loads(SEED.read_text(encoding="utf-8"))
    for spec in definitions:
        # These intents are already in house format — the whole point of
        # carrying them over verbatim. Marking them polished keeps
        # services/automation_intent.py from ever rewriting them.
        spec["intent_status"] = "polished"
    return definitions


async def _create_all(definitions: list[dict], dry: bool) -> tuple[int, int]:
    created = existing = 0
    async with AsyncSessionLocal() as db:
        for spec in definitions:
            spec = dict(spec)
            legacy = spec.pop("legacy_id")
            agent_id = spec.pop("agent_id")
            when = f"{spec['schedule']['frequency']} {spec['schedule']['at']}"
            if dry:
                print(f"  would create  {spec['name']:<22} {agent_id:<12} {when}  ({legacy})")
                created += 1
                continue
            try:
                # model="" — no polish pass. See the module docstring.
                row = await manager.create_automation(spec, db, agent_id=agent_id, model="")
            except ValueError as exc:
                if "already exists" in str(exc):
                    print(f"  exists        {spec['name']:<22} — left alone")
                    existing += 1
                    continue
                print(f"  FAILED        {spec['name']:<22} {exc}")
                return created, -1
            print(f"  created  #{row['id']:<4} {spec['name']:<22} {agent_id:<12} {when}")
            created += 1
    return created, existing


async def _retire_legacy(dry: bool) -> bool:
    """Switch the old combined workflow off. Not deleted — deactivating is
    reversible in one click if a migrated briefing turns out wrong, and the
    execution history stays readable."""
    n8n = N8nClient()
    if not n8n.configured:
        print("!!  n8n is not configured — cannot retire the old workflow.")
        return False
    live = {w.get("name"): w for w in await n8n.list_workflows()}
    wf = live.get(LEGACY_WORKFLOW)
    if wf is None:
        print(f"  '{LEGACY_WORKFLOW}' is not in n8n — nothing to retire.")
        return True
    if not wf.get("active", False):
        print(f"  '{LEGACY_WORKFLOW}' is already inactive.")
        return True
    if dry:
        print(f"  would deactivate '{LEGACY_WORKFLOW}' (id {wf.get('id')})")
        return True
    ok = await n8n.set_active(str(wf["id"]), False)
    print(f"  {'deactivated' if ok else 'FAILED to deactivate'} '{LEGACY_WORKFLOW}'")
    return ok


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="show the plan, change nothing")
    args = ap.parse_args()
    dry = args.dry_run

    definitions = _load()
    print(f"{'DRY RUN — ' if dry else ''}migrating {len(definitions)} briefings\n")

    created, existing = await _create_all(definitions, dry)
    if existing < 0:
        print("\n!!  A create failed. The old workflow was NOT touched, so the "
              "briefings still run from it. Fix the error and re-run.")
        return 1
    if created + existing != len(definitions):
        print("\n!!  Not every briefing was accounted for. Leaving the old "
              "workflow alone.")
        return 1

    print()
    if not await _retire_legacy(dry):
        # In a dry run nothing was created, so there is no double-fire to warn
        # about — only that this step could not be checked from here.
        print("\n!!  Could not reach n8n to check the old workflow." if dry else
              "\n!!  The automations exist but the old workflow is still live — "
              "you would get every briefing TWICE. Switch 'Scheduled briefings' "
              "off in the n8n UI now.")
        return 1

    print("\nDone." if not dry else "\nDry run complete — nothing was changed.")
    if not dry:
        print("Check Settings → Automations; the four briefings should be listed "
              "and editable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
