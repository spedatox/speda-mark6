# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The Octavius Protocol's tool surface.

The work is in app/services/octavius.py. This is how Orion reaches it when the
owner says "back yourself up before I move the server", or asks what protection
actually exists.

WHY `backup` IS NOT OWNER-GATED AND `fetch` IS
-----------------------------------------------
The other host protocols refuse every non-user trigger. This one splits, and the
split follows what the action can destroy:

  * `status` and `list` read. Anyone, any trigger.
  * `backup` CREATES a copy. It cannot lose anything — the worst it does is spend
    some disk and bandwidth and add a file — and the whole reason the protocol
    exists is that the moment you most need a backup is the moment nobody is
    around to authorise one. A watchdog noticing the disk is dying should be able
    to take one without asking.
  * `fetch` stages a restore. It still touches nothing live, but it is the first
    step of overwriting the owner's entire history, and the person deciding to
    roll the brain back to an older copy is the owner. Non-user triggers are
    refused.

The swap itself is in neither list, because nothing here performs it: Igor holds
the database file open, and the service returns instructions rather than doing
it. That is not caution, it is the only correct answer — see the service module.
"""

import logging

from app.config import settings
from app.core.context import AgentContext
from app.skills.base import Skill

logger = logging.getLogger(__name__)

_ACTIONS = ("status", "backup", "list", "fetch")


class OctaviusProtocolSkill(Skill):
    name = "octavius_protocol"
    deferred = True
    search_keywords = (
        "octavius backup brain database restore snapshot drive disaster recovery "
        "migrate move server export dump archive protect data loss"
    )
    restricted_to = frozenset({"orion", "optimus"})
    read_only = False
    requires_network = True
    description = (
        "Runs the OCTAVIUS PROTOCOL — snapshotting Igor's own database and shipping "
        "it to the owner's Google Drive, so a dying host or a move to a new one costs "
        "a download instead of everything Speda knows. Use 'backup' when the owner "
        "says they are about to move, rebuild or shut down the server, before any "
        "risky maintenance, or when 'status' reports that the newest copy has gone "
        "stale; use 'status' whenever they ask whether they are protected, and 'list' "
        "to show what Drive actually holds. Use 'fetch' only when they are restoring: "
        "it downloads a backup, verifies it, and stages it beside the live database "
        "WITHOUT swapping it in, then hands back the exact commands they must run "
        "with the app stopped. Do NOT treat 'backup' as a substitute for the nightly "
        "cron, do NOT attempt the swap yourself with system_ops (this process holds "
        "the database open and replacing it underneath is corruption, not risk), and "
        "do NOT report a backup as taken without reading the result — a failed run "
        "names the stage it stopped at. Returns the sizes, the integrity-check "
        "result, what was uploaded and what old copies were retired."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": list(_ACTIONS),
                "description": (
                    "status: is there a backup worth relying on (asks Drive, "
                    "changes nothing). backup: take one now. list: what Drive "
                    "holds. fetch: download and stage one for a manual restore."
                ),
            },
            "file_id": {
                "type": "string",
                "description": (
                    "fetch only. The backup's Drive id, taken verbatim from a "
                    "'list' you ran in this conversation. Omit it to stage the "
                    "newest. Never invent or reconstruct an id."
                ),
            },
        },
        "required": ["action"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        if not settings.octavius_protocol_enabled:
            return (
                "The Octavius Protocol is disabled on this deployment "
                "(OCTAVIUS_PROTOCOL_ENABLED is off), so NOTHING is being backed "
                "up. Tell the owner that plainly — this is not a tool that "
                "failed, it is protection that does not exist."
            )

        from app.services import octavius

        action = str(args.get("action") or "").strip().lower()
        if action not in _ACTIONS:
            return (
                "REFUSED — nothing was read and nothing was changed: no valid "
                "`action` was given. Call octavius_protocol again with action set "
                f"to exactly one of {', '.join(_ACTIONS)}."
            )

        if action == "status":
            return self._status_report(await octavius.status())

        if action == "list":
            found, err = await octavius.backups()
            if err:
                return f"Could not read the backup folder: {err}"
            if not found:
                return (
                    "Drive holds no backups at all. Tell the owner there is "
                    "currently nothing to restore from."
                )
            lines = [f"{len(found)} backup(s) in Drive, newest first:"]
            for f in found:
                lines.append(f"  {f['created'][:19]}  {f['mb']:>7} MB  {f['name']}")
                lines.append(f"            id: {f['id']}")
            return "\n".join(lines)

        if action == "fetch":
            # Rolling the brain back to an older copy is the owner's decision,
            # even though this step still touches nothing live.
            if context.triggered_by != "user":
                logger.warning("octavius_fetch_non_user",
                               extra={"request_id": context.request_id})
                return (
                    "REFUSED — nothing was downloaded. Staging a restore is the "
                    "first step of replacing the owner's entire history, and can "
                    "only happen in a conversation with them. Report whatever "
                    "prompted this and let them decide."
                )
            ok, report = await octavius.fetch(str(args.get("file_id") or "").strip())
            logger.warning("octavius_fetch", extra={"request_id": context.request_id,
                                                    "ok": ok})
            return report if ok else f"{report}\n\nNothing was staged."

        # backup — creates, never destroys, so a watchdog may take one unasked.
        ok, report = await octavius.backup()
        logger.warning("octavius_backup_run",
                       extra={"request_id": context.request_id, "ok": ok,
                              "stage": report.get("stage", "")})
        return self._backup_report(ok, report)

    def _backup_report(self, ok: bool, r: dict) -> str:
        if not ok:
            stage = r.get("stage", "unknown")
            head = f"BACKUP FAILED at the '{stage}' stage. Nothing was uploaded."
            if stage == "integrity":
                head = (
                    "BACKUP FAILED — and this is worse than a failed backup. The "
                    "snapshot did not pass its integrity check, which is a "
                    "statement about the LIVE DATABASE, not about the copy. Tell "
                    "the owner immediately and do not describe it as a transient "
                    "error."
                )
            return (
                f"{head}\n{r.get('error', '')}\n\n"
                "Report it as FAILED. Do not tell the owner they are backed up."
            )
        lines = [
            f"BACKUP COMPLETE — {r['name']}",
            f"  live database : {r.get('live_bytes', 0) / 1048576:.1f} MB",
            f"  snapshot      : {r.get('snapshot_bytes', 0) / 1048576:.1f} MB "
            "(VACUUM INTO — consistent, compacted)",
            f"  uploaded      : {r.get('archive_bytes', 0) / 1048576:.1f} MB gzipped",
            f"  integrity     : {r.get('integrity', '?')}",
            f"  kept in Drive : {r.get('kept', 0)}",
        ]
        if r.get("trashed"):
            lines.append(f"  retired       : {', '.join(r['trashed'])} (trashed, "
                         "recoverable for 30 days)")
        lines.append(
            "\nNot in it, on purpose: runtime_state.json and the managed .env — the "
            "OAuth tokens and portal credentials. If the owner is MOVING the server, "
            "say so: those two files have to travel by hand, or the integrations get "
            "reconnected on the new host."
        )
        return "\n".join(lines)

    def _status_report(self, s: dict) -> str:
        if not s.get("enabled"):
            return f"Octavius Protocol: disabled. {s.get('detail', '')}".strip()
        if not s.get("count"):
            return (
                "NO BACKUPS EXIST. "
                f"{s.get('detail', '')}\n"
                "Tell the owner there is nothing to restore from and offer to take "
                "one now."
            ).strip()

        latest = s["latest"]
        age = s.get("age_hours")
        lines = [
            f"{s['count']} backup(s) in Drive.",
            f"  newest: {latest['name']} — {latest['mb']} MB, "
            f"{latest['created'][:19]}"
            + (f" ({age}h ago)" if age is not None else ""),
        ]
        if s.get("stale"):
            lines.append(
                "\nSTALE — the newest copy is older than this deployment's "
                "threshold, which at a nightly cadence means runs have been "
                "failing silently. Say so, and offer to take one now."
            )
        if s.get("detail"):
            lines.append(f"\n{s['detail']}")
        return "\n".join(lines)
