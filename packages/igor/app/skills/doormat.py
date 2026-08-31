# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The Doormat Protocol's tool surface.

The protocol lives in app/services/doormat.py; this is how Orion reaches it while
walking the owner through the half nobody here can automate — the DNS record and
the three OAuth consoles.

EVERY PHASE IS OWNER-ONLY
-------------------------
Unlike the Lifeboat Protocol, there is no autonomous path here and no emergency
that would justify one. Changing the domain is never urgent enough to do without
the person whose domain it is, and every phase refuses a non-user trigger — the
same guard carried from app/skills/lockdown.py, where it was a live failure
rather than a hypothetical.

`status` is the exception, because reading is not acting: a background turn is
allowed to notice that a staged domain has been sitting there unfinished for a
week and mention it.

WHY THE TOOL IS SHAPED AS PHASES AND NOT AS "CHANGE THE DOMAIN"
---------------------------------------------------------------
A single `change_domain(new)` would be the friendlier-looking tool and the wrong
one. Between staging and cutover the owner has to go and edit Google Cloud
Console, Azure and Notion by hand, which takes them however long it takes them —
often a day. A tool that did the whole move in one call would either do it before
those consoles knew the new address (breaking sign-in) or block a turn waiting for
a human. The phases ARE the protocol; collapsing them would delete the part that
makes it safe.
"""

import logging

from app.config import settings
from app.core.context import AgentContext
from app.skills.base import Skill

logger = logging.getLogger(__name__)

_ACTIONS = ("status", "stage", "cutover", "retire", "abort")


class DoormatProtocolSkill(Skill):
    name = "doormat_protocol"
    deferred = True
    search_keywords = (
        "doormat domain change move hostname dns caddy certificate tls https "
        "oauth redirect uri migrate address rename site url"
    )
    restricted_to = frozenset({"orion", "optimus"})
    read_only = False
    requires_network = False
    description = (
        "Runs the DOORMAT PROTOCOL — moving Mark VI to a new domain without locking "
        "the owner out of it. Use it when the owner says they are changing the "
        "server's domain, have bought a new one, or want to know where a move they "
        "already started has got to. It works in three deliberate phases: 'stage' "
        "serves the new domain alongside the old one with a real certificate and "
        "changes nothing else, 'cutover' repoints Igor's Telegram webhook base and "
        "OAuth redirect URIs once the owner has updated the third-party consoles, and "
        "'retire' finally stops serving the old hostname. Do NOT use it to set up a "
        "domain for the first time (that is a deploy concern), do NOT run cutover "
        "before the owner confirms Google/Microsoft/Notion have the new redirect URI, "
        "and do NOT run retire until they say the new address works everywhere they "
        "use it. Returns what actually happened to the host, plus the exact strings "
        "the owner must paste into each console."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": list(_ACTIONS),
                "description": (
                    "status: where the move is and what is still outstanding "
                    "(changes nothing). stage: start — serve the new domain "
                    "alongside the current one. cutover: repoint Igor's settings "
                    "to the staged domain. retire: stop serving the old domain. "
                    "abort: undo a stage."
                ),
            },
            "domain": {
                "type": "string",
                "description": (
                    "stage only. The new hostname, bare — speda.example.com. A "
                    "scheme, path or port is trimmed. Pass exactly what the owner "
                    "gave you; never a domain you inferred or completed."
                ),
            },
            "force": {
                "type": "boolean",
                "description": (
                    "stage only, and rarely. Skips the check that the domain "
                    "resolves to this server. The ONE legitimate use is a proxy in "
                    "front (Cloudflare and the like), where the record correctly "
                    "points elsewhere. Never pass it to get past a record that has "
                    "simply not propagated yet — wait instead."
                ),
            },
        },
        "required": ["action"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        if not settings.doormat_protocol_enabled:
            return (
                "The Doormat Protocol is disabled on this deployment "
                "(DOORMAT_PROTOCOL_ENABLED is off). Nothing was read and nothing "
                "was changed. Tell the owner it must be enabled, and that it also "
                "needs the system_ops host bridge configured to reach the host."
            )

        from app.services import doormat

        action = str(args.get("action") or "").strip().lower()
        if action not in _ACTIONS:
            return (
                "REFUSED — nothing was read and nothing was changed: no valid "
                "`action` was given. Call doormat_protocol again with action set "
                f"to exactly one of {', '.join(_ACTIONS)}."
            )

        if action == "status":
            return self._status_report(await doormat.status())

        # Everything else moves the deployment's address. There is no emergency
        # that makes that a thing to do without the owner present.
        if context.triggered_by != "user":
            logger.warning(
                "doormat_non_user_refused",
                extra={"request_id": context.request_id, "action": action},
            )
            return (
                f"REFUSED — nothing was changed. '{action}' moves the server's "
                "domain, and that can only happen in a conversation with the "
                "owner: never from a dispatched task, an automated trigger or "
                "another agent. If something looks wrong with the domain, report "
                "it and let them decide."
            )

        if action == "stage":
            domain = str(args.get("domain") or "").strip()
            if not domain:
                return (
                    "REFUSED — nothing was changed: stage needs the new domain and "
                    "none was given. Ask the owner for it verbatim and call again "
                    "with `domain` set. Do not guess it from anything they said "
                    "earlier."
                )
            ok, report = await doormat.stage(domain, force=bool(args.get("force")))
        elif action == "cutover":
            ok, report = await doormat.cutover()
        elif action == "retire":
            ok, report = await doormat.retire()
        else:
            ok, report = await doormat.abort()

        logger.warning(
            "doormat_action",
            extra={"request_id": context.request_id, "action": action, "ok": ok},
        )
        if not ok:
            return (
                f"{report}\n\nNothing changed. Report this to the owner as it is — "
                "do not describe the move as done or partly done."
            )
        return report

    def _status_report(self, state: dict) -> str:
        if not state.get("enabled"):
            return f"Doormat Protocol: disabled. {state.get('detail', '')}".strip()

        phase = state.get("phase") or ""
        current = state.get("current_domain") or "(unknown)"
        if not phase:
            return (
                f"No domain change in progress. The server is serving {current}.\n"
                "Nothing to report unless the owner asks to move it."
            )

        lines = [
            f"DOORMAT — phase: {phase}",
            f"  moving from : {state.get('previous') or '(unrecorded)'}",
            f"  moving to   : {state.get('target')}",
            f"  Caddy serves: {current}"
            + (f" and {state.get('target')}" if state.get("target_serving") else ""),
            f"  new domain answering: "
            + ("yes" if state.get("target_serving") else f"NO — {state.get('detail', '')}"),
        ]
        if state.get("staged_at"):
            lines.append(f"  staged at   : {state['staged_at']}")
        if state.get("cutover_at"):
            lines.append(f"  cut over at : {state['cutover_at']}")

        if state.get("restart_pending"):
            lines += [
                "",
                "RESTART OUTSTANDING — the cutover settings are written but this "
                "process is still running on the old domain. Nothing else will "
                "work correctly until:",
                '    system_ops(action="restart_service", service="app")',
            ]

        checklist = state.get("checklist") or []
        if phase == "staged" and checklist:
            lines += ["", "Still the owner's to do, before cutover:"]
            for i, step in enumerate(checklist, 1):
                lines.append(f"  {i}. {step['provider']} — {step['where']}")
                lines.append(f"     {step['field']}: {step['value']}")
                if step.get("note"):
                    lines.append(f"     ({step['note']})")
        elif phase == "cutover":
            lines += [
                "",
                "Waiting on the owner to confirm the new address works everywhere "
                "they use it. Then retire the old one — and only then.",
            ]
        return "\n".join(lines)
