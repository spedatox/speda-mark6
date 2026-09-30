# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Read-only traversal of connected owner memory."""

from app.core.context import AgentContext
from app.services.memory_graph import build_context
from app.skills.base import Skill


class ExploreMemorySkill(Skill):
    name = "explore_memory"
    read_only = True
    description = (
        "Follow verified links from a memory observation, event record, person, project, "
        "or document path. Use it after search_memory or memory_event provides an ID when "
        "the question depends on related people, events, decisions, or source messages. "
        "Do not treat co-mentioned facts or shared evidence as causal proof, and do not "
        "use this to write memory. Returns a bounded neighborhood with labelled links "
        "and short source excerpts, scoped to this owner."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "ref": {
                "type": "string",
                "description": (
                    "Stable address: observation:2336, record:<uuid>, "
                    "entity:person:Bedirhan, or path:/memories/..."
                ),
            },
            "depth": {
                "type": "integer", "minimum": 1, "maximum": 3, "default": 2,
                "description": "How many link steps to follow (1–3).",
            },
        },
        "required": ["ref"],
        "additionalProperties": False,
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        ref = str(args.get("ref") or "").strip()
        if not ref.startswith(("observation:", "record:", "entity:", "path:",
                               "revision:", "message:")):
            return "Pass an exact typed memory ref, e.g. observation:2336 or record:<uuid>."
        return await build_context(
            context.db, context.user_id, ref,
            depth=args.get("depth", 2), max_edges=24,
        )
