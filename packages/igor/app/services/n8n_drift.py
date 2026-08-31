# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Shipped-workflow drift probe — is n8n running what the repo says it runs?

`scripts/n8n/*.json` are IMPORT TEMPLATES. n8n keeps its own copy of a workflow
once imported, and nothing links the two: editing the repo file changes nothing
live, and editing in the n8n UI changes nothing in git. There is no error when
they diverge — the automation simply keeps running the older text.

That silence cost a morning. On 2026-08-05 the owner's briefing arrived as a
dashboard of emoji headings and a markdown table, and the health briefing
announced a sync outage. The rewritten intents that would have prevented both
had been committed but never pushed into n8n, so the live `morning_brief` was
still the 1,049-character version that literally instructs the model to LIST
events and to write a `Başlık:` heading. The agent obeyed it perfectly.

A cheap probe, per CLAUDE.md: it answers one deterministic question — "does the
live jsCode equal the committed jsCode" — with zero tokens and no reasoning. It
compares only Code nodes, because that is where every shipped workflow keeps
its editable configuration; node positions, credentials and connection wiring
are deliberately ignored so cosmetic UI edits do not raise a false alarm.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_SHIPPED_DIR = Path(__file__).resolve().parent.parent.parent / "scripts" / "n8n"


def _code_nodes(nodes: list[dict]) -> dict[str, str]:
    """{node name → jsCode} for every Code node in a workflow."""
    return {
        n.get("name", "?"): (n.get("parameters") or {}).get("jsCode", "")
        for n in nodes or []
        if str(n.get("type", "")).endswith("code")
    }


def shipped_workflows() -> dict[str, dict]:
    """{workflow name → parsed template} for every file in scripts/n8n."""
    out: dict[str, dict] = {}
    if not _SHIPPED_DIR.is_dir():
        return out
    for path in sorted(_SHIPPED_DIR.glob("*.json")):
        try:
            wf = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001 — a broken template is a finding, not a crash
            logger.warning("n8n_template_unreadable", extra={"file": path.name, "error": str(e)})
            continue
        name = wf.get("name")
        if name:
            out[name] = wf
    return out


async def scan(client) -> list[dict]:
    """Every shipped workflow whose live copy differs from the committed one.

    Returns [] when everything matches — the empty return IS the cost boundary
    for the n8n gate node. Never raises: a probe that can break the poller is
    worse than one that reports nothing.
    """
    templates = shipped_workflows()
    if not templates:
        return []
    # N8nClient.configured is a PROPERTY, not a method (services/n8n_api.py).
    if not client.configured:
        logger.warning("n8n_drift_unconfigured")
        return []

    try:
        live_list = await client.list_workflows()
    except Exception as e:  # noqa: BLE001
        logger.warning("n8n_drift_list_failed", extra={"error": str(e)})
        return []

    live_by_name = {w.get("name"): w for w in live_list or []}
    drift: list[dict] = []

    for name, template in templates.items():
        live = live_by_name.get(name)
        if live is None:
            drift.append({
                "workflow": name, "reason": "missing",
                "detail": "shipped in scripts/n8n but not present in n8n at all",
            })
            continue

        # list_workflows may omit nodes; fetch the full record when needed.
        nodes = live.get("nodes")
        if nodes is None:
            try:
                full = await client.get_workflow(live.get("id"))
            except Exception as e:  # noqa: BLE001
                logger.warning("n8n_drift_fetch_failed", extra={"workflow": name, "error": str(e)})
                continue
            nodes = (full or {}).get("nodes") or []

        want = _code_nodes(template.get("nodes") or [])
        have = _code_nodes(nodes)
        for node_name, want_js in want.items():
            have_js = have.get(node_name)
            if have_js is None:
                drift.append({
                    "workflow": name, "reason": "node_missing", "node": node_name,
                    "detail": f"Code node {node_name!r} is not in the live workflow",
                })
            elif have_js.strip() != want_js.strip():
                drift.append({
                    "workflow": name, "reason": "code_drift", "node": node_name,
                    "live_chars": len(have_js), "repo_chars": len(want_js),
                    "detail": (
                        f"live jsCode is {len(have_js)} chars, committed is "
                        f"{len(want_js)} — n8n is not running what the repo says"
                    ),
                })

        if not live.get("active", True):
            drift.append({
                "workflow": name, "reason": "inactive",
                "detail": "shipped workflow exists but is switched off in n8n",
            })

    if drift:
        logger.warning(
            "n8n_drift_detected",
            extra={"count": len(drift), "workflows": sorted({d["workflow"] for d in drift})},
        )
    return drift
