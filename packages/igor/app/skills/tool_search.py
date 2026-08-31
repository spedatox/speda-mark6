# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Progressive tool disclosure — the client-side half of Anthropic's pattern.

Anthropic's API does this natively: mark a tool `defer_loading: true`, add the
`tool_search_tool_regex` server tool, and the model searches its own tool
library without the schemas ever sitting in the prefix. Speda runs on whatever
provider the owner routed the agent to, and z.ai/Gemini/OpenAI/DeepSeek have no
such server tool — so this skill is that mechanism, reimplemented over the
CapabilityRegistry so the behaviour is identical on every provider.

The property that makes it worth doing is the one that is easy to lose:
resolved schemas are APPENDED to the tool array, never spliced into it. The
prefix the provider already cached stays byte-identical, so a mid-turn search
costs one round trip instead of invalidating the whole conversation cache. A
naive "rebuild the tools list" implementation would cost more than it saves.
"""

import logging

from app.core.context import AgentContext
from app.skills.base import Skill

logger = logging.getLogger(__name__)


class ToolSearchSkill(Skill):
    name = "tool_search"
    requires_network = False  # searching the local registry needs no uplink
    read_only = True
    description = (
        "Finds and loads tools that are not currently in your tools array. Most "
        "tools are listed by NAME ONLY under 'Additional tools (not yet loaded)' "
        "in your prompt — this is what turns one of those names into a callable "
        "tool. Call it with a few words describing the capability you need (e.g. "
        "'send a telegram message', 'driving route between two places', 'check "
        "gmail') or with an exact tool name, and it returns the matching tools' "
        "full descriptions and input schemas; those tools then become callable "
        "for the rest of the conversation. Search ONCE for everything the task "
        "needs — list several capabilities in one query rather than making a "
        "separate call per tool, since each call is a round trip. Do NOT use it "
        "for tools already present in your tools array, and do NOT use it to "
        "browse; if nothing matches, answer without the tool instead of "
        "searching again with reworded queries."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "What you need the tool to do, in a few words — or an exact "
                    "tool name from the 'Additional tools' list. Combine several "
                    "needs in one query (e.g. 'gmail search and calendar events')."
                ),
            },
        },
        "required": ["query"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        import json

        query = (args.get("query") or "").strip()
        if not query:
            return (
                "No query given. Call tool_search with a short description of the "
                "capability you need, e.g. {\"query\": \"send a telegram message\"}."
            )

        registry = context.extra.get("registry")
        if registry is None:
            return "Tool search is unavailable: the capability registry was not provided."

        matches = registry.search_tools(
            query,
            allowlist=context.extra.get("tool_allowlist"),
            active_servers=context.extra.get("active_servers"),
            agent_id=context.agent_id,
        )
        if not matches:
            return (
                f"No unloaded tool matches '{query}'. Everything else you can use is "
                "already in your tools array — do not search again with different "
                "wording; either use a tool you already have or answer directly."
            )

        # Mark resolved so the next iteration's tool array carries them. A set on
        # the context, so it survives the loop but dies with the turn.
        loaded = context.extra.setdefault("loaded_tools", set())
        for tool in matches:
            loaded.add(tool["name"])

        # Session-sticky, exactly like the toolset memory it replaces: without
        # this the array shrinks back on the next turn, the model re-searches,
        # and the tail of the prefix changes on every turn — the cache-churn
        # this whole mechanism exists to avoid.
        sticky = context.extra.get("mark_tools_loaded")
        if callable(sticky):
            sticky({t["name"] for t in matches})

        logger.info(
            "tool_search",
            extra={
                "request_id": context.request_id,
                "query": query,
                "matched": [t["name"] for t in matches],
            },
        )

        # `_keywords` is a search-only marker — never show it to the model.
        rendered = "\n\n".join(
            f"### {t['name']}\n{t.get('description', '')}\n\n"
            f"Input schema:\n```json\n{json.dumps(t.get('input_schema', {}), indent=2)}\n```"
            for t in matches
        )
        return (
            f"Loaded {len(matches)} tool(s) — they are now callable and will stay "
            f"available for the rest of this conversation.\n\n{rendered}"
        )
