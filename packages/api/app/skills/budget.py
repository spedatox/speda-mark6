import logging

from app.skills.base import Skill
from app.core.context import AgentContext
from app.core.runtime_state import get_budget_mode, set_budget_mode

logger = logging.getLogger(__name__)


class BudgetModeSkill(Skill):
    name = "set_budget_mode"
    description = (
        "Turns SPEDA's budget mode ON or OFF when the owner asks (e.g. 'go budget "
        "mode', 'activate budget mode', 'stop saving money', 'unleash yourself', "
        "'turn off budget mode'). Budget mode forces short answers, minimises web "
        "searches, and disables expensive sub-agents. Use this ONLY when the owner "
        "clearly intends to change the cost/verbosity setting — not for ordinary "
        "requests. The change persists across restarts and takes effect from the "
        "next turn. Returns a confirmation of the new state."
    )
    read_only = False
    input_schema = {
        "type": "object",
        "properties": {
            "enabled": {
                "type": "boolean",
                "description": "True to enable budget mode (frugal), False to disable it (full power).",
            }
        },
        "required": ["enabled"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        enabled = bool(args.get("enabled", True))
        was = get_budget_mode()
        set_budget_mode(enabled)
        logger.info(
            "budget_mode_toggled_by_agent",
            extra={"request_id": context.request_id, "from": was, "to": enabled},
        )
        if enabled:
            return (
                "Budget mode is now ON. From the next turn: concise answers, minimal "
                "searches, sub-agents disabled. Tell me to turn it off when you want "
                "full power again, sir."
            )
        return (
            "Budget mode is now OFF. Full capabilities restored — deep research and "
            "sub-agents are available again. I'll spend where it counts, sir."
        )
