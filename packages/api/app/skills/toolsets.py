import logging

from app.skills.base import Skill
from app.core.context import AgentContext

logger = logging.getLogger(__name__)


class UseToolsetSkill(Skill):
    name = "use_toolset"
    description = (
        "Loads a toolset so its tools become usable. Most tools (Gmail, Calendar, "
        "Notion, etc.) are NOT loaded by default — the list of loadable toolsets is "
        "in your system prompt under 'Loadable toolsets'. Call this with the server "
        "name (e.g. 'google_gmail', 'notion') the moment a task needs it, then use "
        "that toolset's tools — they only become callable AFTER you load them. "
        "Loading is cheap; load just what the task needs. You can load several. "
        "Always-on tools (like web search) need no loading."
    )
    read_only = False
    input_schema = {
        "type": "object",
        "properties": {
            "server": {
                "type": "string",
                "description": "The toolset/server name to load, e.g. 'google_gmail', 'google_calendar', 'notion'.",
            },
        },
        "required": ["server"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        server = (args.get("server") or "").strip()
        if not server:
            return "No toolset name given. See 'Loadable toolsets' in your prompt."
        active = context.extra.setdefault("active_servers", set())
        if not isinstance(active, set):
            active = set(active)
            context.extra["active_servers"] = active
        active.add(server)
        logger.info("toolset_loaded", extra={"request_id": context.request_id, "server": server})
        return (
            f"Loaded the '{server}' toolset — its tools are now available this turn. "
            f"Call the tool you need now."
        )
