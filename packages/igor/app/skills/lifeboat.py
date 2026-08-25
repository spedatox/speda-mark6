"""
The Lifeboat Protocol's tool surface — and the authorization boundary on it.

Reclamation itself lives in app/services/lifeboat.py; the runbook that says what
each tier costs is docs/LIFEBOAT_PROTOCOL.md. This file exists for one reason:
to decide who is allowed to pull which lever.

THE OWNER LEADS
---------------
The original runbook let Orion bail unattended at any pressure. The owner has
since narrowed it, and this is where the narrowing is enforced rather than
merely written down:

  * `assess` reads and reclaims nothing. Always allowed.
  * `bail` (Tier 1 — Docker build cache, stopped cells, dangling layers,
    journald, stale outputs) runs without the owner ONLY when the host is
    verified critical. Below that it needs them in the conversation. The reason
    for the exception is the one case where asking loses: a disk at 97% at 04:00
    takes the whole box down before anyone reads a notification, and Tier 1
    deletes nothing that was not already garbage.
  * `jettison` and `restore` are always the owner's. Tier 2 costs a 45-minute
    rebuild, and a 45-minute rebuild is not a decision a watchdog gets to make.

CRITICALITY IS RE-DERIVED, NEVER READ FROM THE PAYLOAD
------------------------------------------------------
The autonomous path is unlocked by asking the HOST, in this turn, through
services/lifeboat.assess(). It is never unlocked by a field in the trigger
payload saying the host is critical — that payload is attacker-shaped input
arriving from an automation surface, and "the disk is full, please prune" is
precisely the sentence it would carry. The extra round trip is the price of the
gate meaning something.

The `triggered_by != "user"` guard is carried over verbatim from
app/skills/lockdown.py, which carries it from HousePartySkill, where it was a
live failure rather than a hypothetical: a dispatched agent was handed an
"EMERGENCY" instruction as an ordinary inter-agent task. Orion refused on
judgement — the right call, but judgement is not a control.
"""

import logging

from app.config import settings
from app.core.context import AgentContext
from app.skills.base import Skill

logger = logging.getLogger(__name__)

_OWNER_ONLY = (
    "REFUSED — nothing was reclaimed and the host is exactly as it was. {what} "
    "can only be run in a conversation with the owner, never from a dispatched "
    "task or an automated trigger. Report the assessment to them and let them "
    "make the call."
)


class LifeboatProtocolSkill(Skill):
    name = "lifeboat_protocol"
    deferred = True
    search_keywords = (
        "lifeboat disk space full storage cleanup reclaim prune docker cache "
        "out of space server resources memory pressure inodes maintenance "
        "housekeeping capacity"
    )
    restricted_to = frozenset({"orion", "optimus"})
    read_only = False
    requires_network = False
    description = (
        "Runs the LIFEBOAT PROTOCOL — the tiered reclamation that keeps the Mark VI "
        "host from running out of disk. Use 'assess' whenever the owner asks how the "
        "server is doing on space, when a resource-pressure watchdog wakes you, or "
        "before proposing any cleanup: it reads disk, inodes, memory, swap and the "
        "Docker footprint in one call and returns the numbers plus what it would "
        "recommend. Use 'bail' for Tier 1 — Docker build cache, stopped cells, "
        "dangling layers, journald and stale outputs — which touches no running "
        "service and is reversible; use 'jettison' only when the owner has explicitly "
        "agreed to throw the ~25 GB Kali arsenal overboard, and 'restore' to rebuild "
        "it once space is healthy. Do NOT use this for memory pressure (the script "
        "reclaims disk; a leaking container is a system_ops restart), do NOT use it on "
        "any machine other than this host, and do NOT run 'bail' or 'jettison' on your "
        "own initiative when the owner is not asking — below a verified critical level "
        "the tool refuses them, and above it you must still report what you did. "
        "Returns the script's own output: what was reclaimed, what remains, and what "
        "it refused to touch."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["assess", "bail", "jettison", "restore"],
                "description": (
                    "assess: read the host and report, changes nothing. bail: "
                    "Tier 1 reclamation of throwaway Docker junk and old logs. "
                    "jettison: Tier 2, drop the ~25 GB Kali arsenal (owner's "
                    "explicit word required). restore: rebuild the arsenal once "
                    "disk is healthy again (a 45-minute bake)."
                ),
            },
            "hot_spots": {
                "type": "boolean",
                "description": (
                    "assess only. Adds a `du` breakdown of what is actually "
                    "filling the disk. Costs seconds, so ask for it when the "
                    "cheap tiers have already run and the box is STILL full — "
                    "that is the case where something abnormal is filling it and "
                    "the owner needs the breakdown to decide."
                ),
            },
        },
        "required": ["action"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        if not settings.lifeboat_protocol_enabled:
            return (
                "The Lifeboat Protocol is disabled on this deployment "
                "(LIFEBOAT_PROTOCOL_ENABLED is off). Nothing was read and nothing "
                "was changed. Tell the owner it must be enabled, and that it also "
                "needs the system_ops host bridge configured to reach the host."
            )

        from app.services import lifeboat

        action = str(args.get("action") or "").strip().lower()
        if action not in {"assess", "bail", "jettison", "restore"}:
            return (
                "REFUSED — nothing was read and nothing was reclaimed: no valid "
                "`action` was given. Call lifeboat_protocol again with action set "
                "to exactly one of assess, bail, jettison or restore."
            )

        # Every path starts by reading the host. assess needs it as the answer;
        # bail needs it as the authorization; jettison and restore need it so the
        # report can say what the numbers were before the script ran.
        state = await lifeboat.assess()
        if state["status"] != "ok":
            return (
                f"Could not read the host, so nothing was attempted: "
                f"{state.get('detail', 'unknown error')}. Do not tell the owner "
                "anything about the server's disk state — you do not have it."
            )

        if action == "assess":
            return await self._assess_report(state, bool(args.get("hot_spots")), lifeboat)

        # ── The gates ────────────────────────────────────────────────────────
        if action in {"jettison", "restore"}:
            if context.triggered_by != "user":
                logger.warning(
                    "lifeboat_owner_only_refused",
                    extra={"request_id": context.request_id, "action": action},
                )
                return _OWNER_ONLY.format(
                    what="Tier 2 (jettison) and the arsenal rebuild (restore)"
                ) + f"\n\nCurrent state: {state['summary']}"

        if action == "bail" and context.triggered_by != "user":
            # The one autonomous path, and it is unlocked by the HOST, not by
            # whatever the trigger payload claimed about it.
            if state["level"] != lifeboat.CRITICAL:
                logger.info(
                    "lifeboat_autonomous_bail_declined",
                    extra={"request_id": context.request_id, "level": state["level"]},
                )
                return (
                    "REFUSED — nothing was reclaimed. Tier 1 runs on its own only "
                    f"when the host is CRITICAL; it is currently '{state['level']}'. "
                    "Report this to the owner and ask whether to bail:\n\n"
                    f"{state['summary']}\n\n{state['recommendation']}"
                )
            logger.warning(
                "lifeboat_autonomous_bail",
                extra={"request_id": context.request_id, "level": state["level"],
                       "disk_pct": state["readings"].get("disk_pct")},
            )

        runner = {"bail": lifeboat.bail, "jettison": lifeboat.jettison,
                  "restore": lifeboat.restore}[action]
        ok, output = await runner()

        after = await lifeboat.assess()
        logger.warning(
            "lifeboat_ran",
            extra={"request_id": context.request_id, "action": action, "ok": ok,
                   "before": state["level"], "after": after.get("level")},
        )

        if not ok:
            return (
                f"The Lifeboat '{action}' did NOT complete. Report it to the owner "
                f"as FAILED — do not claim anything was reclaimed.\n\n{output}\n\n"
                f"State now: {after.get('summary', 'unreadable')}"
            )

        return (
            f"Lifeboat '{action}' ran.\n\n"
            f"BEFORE: {state['summary']}\n"
            f"AFTER:  {after.get('summary', 'unreadable')}\n\n"
            f"--- script output ---\n{output}\n\n"
            "Report to the owner what was reclaimed and what is still pressed. If "
            "the box is still not healthy, do NOT escalate a tier on your own — "
            "give them the numbers and the next option."
        )

    async def _assess_report(self, state: dict, want_hot_spots: bool, lifeboat) -> str:
        readings = state["readings"]
        lines = [
            f"LIFEBOAT ASSESSMENT — level: {state['level'].upper()}",
            state["summary"],
            "",
            f"disk    {readings['disk_pct']}%  ({readings['disk_free_gb']} GB free "
            f"of {readings['disk_total_gb']} GB on {readings['filesystem']})",
            f"inodes  {readings['inode_pct']}%",
            f"memory  {readings['mem_pct']}%  ({readings['mem_available_gb']} GB "
            f"available of {readings['mem_total_gb']} GB)",
            f"swap    {readings['swap_pct']}%  (of {readings['swap_total_gb']} GB)",
        ]
        if readings.get("docker"):
            lines.append("")
            lines.append("Docker footprint:")
            for row in readings["docker"]:
                lines.append(f"  {row['type']:<16} {row['size']:>10}   "
                             f"reclaimable {row['reclaimable']}")
            lines.append(f"  → {readings['docker_reclaimable_gb']} GB reclaimable in total")

        lines += ["", f"Recommendation: {state['recommendation']}"]

        if want_hot_spots:
            lines += ["", "What is actually on the disk (du, one level):",
                      await lifeboat.hot_spots()]

        if state["level"] == lifeboat.HEALTHY:
            lines.append(
                "\nNothing needs doing. Say so in one line rather than proposing "
                "maintenance the box does not need."
            )
        else:
            lines.append(
                "\nThis is an ASSESSMENT — nothing has been reclaimed. Give the "
                "owner the numbers and the recommendation and let them decide."
            )
        return "\n".join(lines)
