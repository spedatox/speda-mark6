# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Executable routing contract. Document grammar remains in memory_spec.

Subject ownership and temporal meaning are separate axes: an event does not
become a state because it happened recently, and an ownership refusal never
changes the subject of a fact. No model or keyword classifier lives here.
"""

from dataclasses import dataclass

from app.services.memory_spec import COLLECTIONS, spec_for


@dataclass(frozen=True)
class Route:
    kind: str
    destination: str
    tool: str
    rule: str


ROUTES = (
    Route("state", "/memories/states/<stable-key>.md", "memory_state",
          "An ongoing situation, unresolved commitment or confirmed future plan. Requires evidence, review date and an explicit status; current.md is its computed view."),
    Route("event", "subject's dated log; otherwise /memories/events/<YYYY-MM>.md", "ledger_append / registry_upsert",
          "Something that happened. Keep the date and outcome. Never promote an action to active context merely because it is recent."),
    Route("preference", "/memories/dossier/<topic>.md", "memory_edit",
          "An owner-stated standing preference or prohibition, dated and attributed. Never an inferred psychological claim."),
    Route("pattern", "/memories/patterns.md", "record_observation + memory",
          "A fallible inference with cited evidence, confidence and a useful response. Keep separate from owner instructions."),
    Route("person", "/memories/social/<professional|personal>/<name>.md", "registry_upsert",
          "One person, stable identity and dated events. Reuse the existing path; organisations are context, not categories."),
    Route("project", "/memories/projects/<name>.md", "registry_upsert",
          "One project's description, decisions and event history. Reuse its existing identity."),
    Route("reference", "owning domain's topic file", "memory_edit",
          "Subject decides the domain. Preserve tables, units and relationships. Reissued editions replace the existing topic through its revision trail."),
    Route("biography", "/memories/owner.md", "narrative_revise",
          "Durable life history. Correct only the relevant chapter; never regenerate the biography from extracted facts."),
)


def routing_contract() -> str:
    rows = ["## Memory routing contract", "", "Classify meaning BEFORE choosing a file."]
    rows += [f"- {r.kind}: {r.destination}; use {r.tool}. {r.rule}" for r in ROUTES]
    rows += ["", "Domain ownership (read access is shared):"]
    rows += [f"- {c.root}/: {c.owner_agent or 'shared'} — {c.summary}"
             for c in COLLECTIONS if c.owner_agent]
    rows += [
        "An ownership refusal means hand off to that domain's owner. It NEVER means put the same fact in current.md, life/, or another shared file.",
        "Split compound updates: the completed action is an event; a resulting unresolved situation is a separate state linked to its evidence.",
        "When uncertain, retain evidence in the conversation/search record and report the uncertainty. Do not invent a destination or overwrite a different subject.",
        "A search observation is a sourced claim, not a second editable copy of a document. Documents retain their native structure.",
    ]
    return "\n".join(rows)


def protected_write(path: str, author: str, *, managed: bool = False) -> str | None:
    """Hard boundaries, shared by raw tools and shaped tools."""
    if path.startswith("/memories/finance/ledger/") or path in ("/memories/finance/monthly-structure.md", "/memories/finance/balances.md", "/memories/finance/reports.md"):
        return "Financial views are computed. Use finance_record; never insert monthly activity into recurring rules."
    if path == "/memories/current.md":
        return "current.md is a computed view. Use memory_state for ongoing situations; ledger_append or registry_upsert for completed events."
    if path.startswith("/memories/states/") and not managed:
        return "State records require memory_state (status, evidence, review date and version). Raw edits would bypass their lifecycle."
    if author == "owner":
        return None
    if path.startswith("/memories/.audit/"):
        return "Audit reports are system-generated from actual document reviews. Use memory_audit operation=run/status."
    if "/." in path and not (author == "orion" and path.startswith("/memories/.audit/")):
        return "Internal archives and system metadata are not agent-writeable. Orion may append its audit under /memories/.audit/."
    spec = spec_for(path)
    if spec and spec.kind == "system":
        return "This trail is system-written; record owner knowledge in its subject's document."
    return None
