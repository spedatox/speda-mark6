"""Progressive disclosure backed by the authoritative capability registry."""
import json
from app.skills.base import Skill


class ToolSearchSkill(Skill):
    name = "tool_search"
    description = (
        "Finds authorized domain tools by name or purpose and loads their schemas for this turn. "
        "Use it when the requested capability is in the available domain index but its schema is absent. "
        "Pass the exact tool name when known, such as calendar_list_events for reading events. "
        "Do not use it for remembering personal history; use injected memory or memory_lookup. "
        "Returns up to five matching tool definitions; it never executes them or grants new permissions."
    )
    input_schema = {"type": "object", "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 200}}, "required": ["query"], "additionalProperties": False}
    read_only = True

    def __init__(self, registry):
        self.registry = registry

    async def execute(self, args, context):
        found = self.registry.search_tools(args["query"], context)
        loaded = set(context.extra.get("loaded_tools", ()))
        loaded.update(tool["name"] for tool in found)
        context.extra["loaded_tools"] = loaded
        return json.dumps(found, ensure_ascii=False)
