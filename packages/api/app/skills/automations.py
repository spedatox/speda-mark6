import json
import logging

from app.core.context import AgentContext
from app.skills.base import Skill
from app.automations import manager

logger = logging.getLogger(__name__)


class AutomationsSkill(Skill):
    name = "manage_automations"
    description = (
        "Creates, lists, pauses/resumes, and deletes SPEDA's proactive watchers — "
        "n8n workflows that monitor things for the owner and ping them on Telegram "
        "when something happens (a page changes, exam results appear, a feed posts, "
        "a schedule fires). Use it when the owner asks you to watch/track/monitor "
        "something, remind them on a schedule, or asks what you're currently "
        "watching ('what are you tracking for me?', 'stop watching X'). Do NOT use "
        "it for one-off questions you can answer right now, or for in-conversation "
        "reminders that don't need future execution. For action='create' you compose "
        "a spec: kind 'web_watch' (url, optional look_for keyword, interval_minutes, "
        "default 360) fires when the page changes or the keyword first appears; "
        "'rss_watch' (feed_url) fires on new feed items; 'schedule' (cron, 5-field) "
        "fires on the cron — phrase intent as the briefing you should deliver; "
        "'webhook' creates an inbound URL other systems can call. Add duration_days "
        "for time-boxed tracking ('for a month' → 30) and always write intent as a "
        "clear instruction to your future self about what to tell the owner and why "
        "it matters. Returns the created/affected automation as JSON, the full list "
        "for 'list', or an error message you should fix and retry (e.g. missing "
        "url, n8n not configured)."
    )
    read_only = False
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "list", "pause", "resume", "delete"],
                "description": "What to do. 'list' shows all watchers with ids; pause/resume/delete need automation_id from a prior list.",
            },
            "automation_id": {
                "type": "integer",
                "description": "Target automation id (from 'list') — required for pause, resume, and delete.",
            },
            "spec": {
                "type": "object",
                "description": (
                    "For 'create': the watcher spec. Required: kind "
                    "(web_watch|rss_watch|schedule|webhook), name (short human label), "
                    "intent (instruction for the future notification). Per kind: "
                    "web_watch → url, optional look_for, optional interval_minutes; "
                    "rss_watch → feed_url, optional interval_minutes; schedule → cron "
                    "(e.g. '0 8 * * *' for 8am daily); webhook → optional webhook_path. "
                    "Optional for all: duration_days (auto-expiry)."
                ),
                "properties": {
                    "kind": {"type": "string", "enum": ["web_watch", "rss_watch", "schedule", "webhook"]},
                    "name": {"type": "string"},
                    "intent": {"type": "string"},
                    "url": {"type": "string"},
                    "look_for": {"type": "string"},
                    "feed_url": {"type": "string"},
                    "cron": {"type": "string"},
                    "interval_minutes": {"type": "integer"},
                    "duration_days": {"type": "number"},
                    "webhook_path": {"type": "string"},
                },
            },
        },
        "required": ["action"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        action = args.get("action")
        if context.db is None:
            return "Automation management needs a database session — none available on this context."
        try:
            if action == "create":
                spec = args.get("spec") or {}
                result = await manager.create_automation(spec, context.db, context.agent_id)
                logger.info(
                    "automation_created_by_agent",
                    extra={"request_id": context.request_id, "automation_id": result["id"]},
                )
                return (
                    "Automation created and live in n8n:\n"
                    + json.dumps(result, indent=2)
                    + "\nConfirm to the owner in one sentence what you're now watching and until when."
                )

            if action == "list":
                items = await manager.list_automations(context.db)
                if not items:
                    return "No automations exist yet. Nothing is being watched."
                return "Current automations:\n" + json.dumps(items, indent=2)

            if action in ("pause", "resume"):
                aid = args.get("automation_id")
                if aid is None:
                    return "pause/resume needs automation_id — call action='list' first to get ids."
                result = await manager.set_automation_active(int(aid), action == "resume", context.db)
                return f"Automation {aid} is now {'active' if result['active'] else 'paused'}:\n" + json.dumps(result, indent=2)

            if action == "delete":
                aid = args.get("automation_id")
                if aid is None:
                    return "delete needs automation_id — call action='list' first to get ids."
                result = await manager.delete_automation(int(aid), context.db)
                return f"Deleted automation '{result['name']}' (id {aid}) and its n8n workflow."

            return f"Unknown action {action!r} — use create, list, pause, resume, or delete."

        except ValueError as exc:
            # Actionable composition/infrastructure error — surface for repair.
            return f"Automation error: {exc}"
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "automation_skill_error",
                extra={"request_id": context.request_id, "error": str(exc)},
            )
            return f"Automation system error: {exc}"
