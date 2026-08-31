# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The shape-aware memory write tools (docs/MEMORY_ARCHITECTURE_V4.md §3.2).

Three verbs, each valid only for one kind of document. They exist because
`str_replace` edits memory as a flat string: the anchor matches, and the content
lands wherever that anchor happened to be. That is how a July expense ends up
under June and how Erasmus notes end up between two lines about HTML template
placeholders.

Here the agent names a PLACE — a month, a date, a person, a chapter — and the
placement is derived from it. Supplying the wrong position stops being possible,
because no position is ever supplied.

All three go out through the same gate as a hand write: ownership, the verifier
and the size caps apply unchanged. This layer decides where; memory_schema
decides whether.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.context import AgentContext
from app.models.memory_file import MemoryFile
from app.services.memory_schema import MemorySchemaViolation, check_write
from app.services.memory_spec import COLLECTIONS, SPECS, route_ledger, spec_for
from app.services.memory_store import record_revision
from app.services.memory_write import (
    WriteRejected,
    ledger_append,
    narrative_revise,
    registry_upsert,
)
from app.skills.base import Skill

logger = logging.getLogger(__name__)


async def _load(context: AgentContext, path: str) -> MemoryFile | None:
    return (
        await context.db.execute(
            select(MemoryFile).where(
                MemoryFile.user_id == context.user_id, MemoryFile.path == path
            )
        )
    ).scalar_one_or_none()


async def _commit(
    context: AgentContext, path: str, before: str, after: str, action: str
) -> str:
    """Gate, persist and record one shaped write. Returns the tool result."""
    try:
        warnings = check_write(
            path=path, before=before, after=after,
            is_create=not before, author=context.agent_id,
        )
    except MemorySchemaViolation as e:
        return str(e)

    file = await _load(context, path)
    if file is None:
        context.db.add(MemoryFile(user_id=context.user_id, path=path, content=after))
    else:
        file.content = after
        file.updated_at = datetime.now(timezone.utc)
    await record_revision(
        context.db, user_id=context.user_id, path=path, author=context.agent_id,
        action=action, before=before, after=after, request_id=context.request_id,
    )
    await context.db.commit()
    logger.info(
        "memory_shaped_write",
        extra={"request_id": context.request_id, "agent": context.agent_id,
               "path": path, "action": action, "delta": len(after) - len(before)},
    )
    note = ("\n\nNote:\n  - " + "\n  - ".join(warnings)) if warnings else ""
    return f"Written to {path}.{note}"


def _ledger_paths() -> str:
    """The dated members an agent can actually append to, named in full.

    Listing the domains (`finance.md`, `wellness.md`) stopped being useful when
    they became directories: a domain no longer has one place a dated entry
    goes. These are the files that DO index a key, so the description names
    exactly what the tool will accept.
    """
    out = [
        f"{c.root.split('/')[-1]}/{m.stem}.md ({m.index_pattern})"
        for c in COLLECTIONS if c.closed
        for m in c.members if m.index_pattern
    ]
    out += [
        p.split("/")[-1] for p, s in SPECS.items()
        if s.kind == "ledger" and not s.superseded_by
    ]
    return ", ".join(out)



class LedgerAppendSkill(Skill):
    name = "ledger_append"
    description = (
        "Add an entry to a ledger document under the period or topic it belongs to — "
        f"the ledgers are {_ledger_paths()}. Use it for every dated or periodic record: "
        "a transaction in a month, a training session on a date, an operational action. "
        "You give the KEY (the month `2026-08`, the date `2026-08-09`) and the entry; the "
        "tool finds or creates that section and puts the entry inside it, so a row can "
        "never land under the wrong month the way a `str_replace` on a nearby anchor can. "
        "Do NOT use it for facts about a person or project (that is `registry_upsert`) or "
        "for a document you do not own — a write to another agent's ledger is refused. "
        "Returns confirmation, or an explanation of what was wrong with the entry."
    )
    read_only = False
    input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "The ledger file, e.g. /memories/wellness/sessions.md or "
                    "/memories/finance/ledger.md. Naming the domain alone "
                    "(`wellness`) also works for a dated entry — the key says "
                    "which file it belongs in."
                ),
            },
            "key": {
                "type": "string",
                "description": (
                    "The period this entry belongs to — a month `YYYY-MM` for finance, "
                    "a date `YYYY-MM-DD` for a session or action log. Absolute only."
                ),
            },
            "section": {
                "type": "string",
                "description": (
                    "For table ledgers, which table: Incomes, Expenses or Debts in "
                    "finance.md. Omit for ledgers that keep bullets."
                ),
            },
            "row": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "One table row, one value per column, in the table's own column "
                    "order. A short row is refused — it would shift every cell after it."
                ),
            },
            "lines": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "For bullet ledgers: the entry's lines (an exercise, a note, an "
                    "action). Written as bullets under the key."
                ),
            },
        },
        "required": ["path", "key"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        key = (args.get("key") or "").strip()
        # A ledger is a directory now, and the KEY says which file: only
        # `sessions` indexes YYYY-MM-DD, only `ledger` indexes YYYY-MM. So an
        # agent naming the domain still lands in the right member instead of
        # being refused for addressing a folder.
        path, error = route_ledger(args.get("path") or "", key)
        if error:
            return error
        spec = spec_for(path)
        if spec is None:
            return f"`{path}` is not a known memory document."
        file = await _load(context, path)
        if file is None:
            return f"`{path}` does not exist."
        try:
            after = ledger_append(
                file.content, path=path, key=key,
                section=(args.get("section") or None),
                row=args.get("row"), lines_in=args.get("lines"),
            )
        except WriteRejected as e:
            return str(e)
        return await _commit(context, path, file.content, after, "ledger_append")


class RegistryUpsertSkill(Skill):
    name = "registry_upsert"
    description = (
        "Create or update one person or one project. Every person and every project has "
        "its OWN file (`/memories/social/<category>/<name>.md`, "
        "`/memories/projects/<name>.md`), and this tool works out which file that is from "
        "the name you give it — you never pass a path, and you never need to know how the "
        "filename is spelled. Use it whenever you learn something about a specific person "
        "or project: `who` sets or replaces their description, `event` adds a dated entry "
        "to their log, newest first; if they have no file yet one is created with the full "
        "required shape, so a person can never end up recorded without a Who block. Do NOT "
        "use it for periodic records like a transaction or a training session (that is "
        "`ledger_append`), and do NOT invent a category — the categories are fixed. "
        "Returns confirmation and the file written, or which categories are valid if you "
        "named one that is not."
    )
    read_only = False
    input_schema = {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": ["person", "project"],
                "description": "Whether this is a person or a project.",
            },
            "entity": {
                "type": "string",
                "description": (
                    "The person's or project's name. Use their existing spelling if they "
                    "are already recorded — a different spelling creates a second file."
                ),
            },
            "category": {
                "type": "string",
                "description": (
                    "For a person only, which group they belong to: Professional, "
                    "Siberay Board or Personal. Required there; ignored for a project."
                ),
            },
            "who": {
                "type": "string",
                "description": (
                    "Who they are and their context to the owner. Replaces the existing "
                    "description — pass the full updated sentence, not a fragment."
                ),
            },
            "event": {
                "type": "string",
                "description": "A dated thing that happened, added to the top of their log.",
            },
            "when": {
                "type": "string",
                "description": "The event's date, YYYY-MM-DD. Defaults to today.",
            },
        },
        "required": ["kind", "entity"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        from app.services.memory_spec import collection_by_root, member_path

        kind = (args.get("kind") or "").strip().lower()
        roots = {"person": "/memories/social", "project": "/memories/projects"}
        if kind not in roots:
            return "`kind` must be either 'person' or 'project'."
        coll = collection_by_root(roots[kind])

        entity = (args.get("entity") or "").strip()
        if not entity:
            return "`entity` is required — who or what is this about?"

        try:
            path = member_path(coll, entity, args.get("category") or None)
        except ValueError as e:
            return str(e)

        # A missing file is the normal case for a new entity, not an error: the
        # folder IS the taxonomy entry, so one more file in it is expected.
        file = await _load(context, path)
        before = file.content if file else ""
        try:
            after = registry_upsert(
                before, path=path, entity=entity,
                category=(args.get("category") or None),
                who=(args.get("who") or None),
                event=(args.get("event") or None),
                when=(args.get("when") or None),
            )
        except WriteRejected as e:
            return str(e)
        return await _commit(context, path, before, after, "registry_upsert")


class NarrativeReviseSkill(Skill):
    name = "narrative_revise"
    description = (
        "Rewrite ONE chapter of the owner's biography (/memories/owner.md) and nothing "
        "else. Use it only when a chapter is factually wrong or genuinely incomplete — "
        "the biography is the owner's life story, built over months, and a chapter is the "
        "widest change this tool will make. Do NOT use it to add a new fact that belongs "
        "in current.md, dossier.md or a domain document, do NOT use it to tidy prose you "
        "find untidy, and do NOT rewrite a chapter you have not been asked to correct. "
        "Pass the chapter's exact heading and its full new body. Returns confirmation, or "
        "the list of chapters if the one you named does not exist."
    )
    read_only = False
    restricted_to = frozenset({"orion", "speda"})
    input_schema = {
        "type": "object",
        "properties": {
            "chapter": {
                "type": "string",
                "description": "The chapter's exact heading, e.g. 'Communication style'.",
            },
            "body": {
                "type": "string",
                "description": "The chapter's complete new body. Replaces everything under that heading.",
            },
        },
        "required": ["chapter", "body"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        path = "/memories/owner.md"
        file = await _load(context, path)
        if file is None:
            return f"`{path}` does not exist."
        try:
            after = narrative_revise(
                file.content, path=path,
                chapter=(args.get("chapter") or "").strip(),
                body=args.get("body") or "",
            )
        except WriteRejected as e:
            return str(e)
        return await _commit(context, path, file.content, after, "narrative_revise")
