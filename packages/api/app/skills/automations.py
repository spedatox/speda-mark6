import json
import logging

from app.core.context import AgentContext
from app.skills.base import Skill
from app.automations import manager

logger = logging.getLogger(__name__)


class AutomationsSkill(Skill):
    name = "manage_automations"
    description = (
        "Your CONTROL PLANE over n8n — the engine that runs every proactive watcher "
        "(a page changes, exam results appear, a feed posts, a schedule fires). You "
        "do NOT write n8n JSON, hit n8n endpoints, or manage webhooks yourself: you "
        "hand this tool a structured `spec` and it composes the correct n8n workflow, "
        "POSTs it, and activates it. n8n is the sole scheduler — never promise an "
        "internal timer or a one-off delay; if it must happen later, it is a watcher. "
        "Use this when the owner asks you to watch/track/monitor something, remind "
        "them on a schedule, or asks what you're watching ('what are you tracking?', "
        "'stop watching X'). Do NOT use it for one-off questions you can answer now, "
        "or in-conversation reminders that don't outlive the chat. "
        "HOW A WATCHER WORKS: when it fires, n8n calls back into POST /trigger/<your "
        "agent_id> with output_mode='push' carrying your `intent` and the event data, "
        "and YOU (this same agent) run it — with no human in the loop. So write "
        "`intent` as an EXECUTABLE instruction to your future self, not a description "
        "of a message. Name the exact tools/steps to run and the real data to gather "
        "(e.g. 'use_toolset google_gmail then list today's important mail', "
        "'news_headlines for the last 24h', 'system_info all for disk/memory/uptime'), "
        "and make clear each part of the output must come from actual tool results — "
        "never invented. Your reply text on a push is auto-delivered to the owner, so "
        "the intent must NOT tell you to call send_telegram_message (that double-sends). "
        "IDEMPOTENCY: before creating a scheduled watcher, action='list' first and "
        "REUSE or delete+replace an existing one for the same job — never stack "
        "duplicates. "
        "KINDS for action='create': 'schedule' (cron, 5-field e.g. '0 8 * * *' = 08:00 "
        "daily) fires on the clock; 'web_watch' (url, optional look_for keyword, "
        "interval_minutes default 360) fires when the page changes or the keyword first "
        "appears; 'rss_watch' (feed_url, optional interval_minutes) fires on new items; "
        "'webhook' creates an INBOUND URL an external system POSTs to — the result "
        "includes 'webhook_url'; relay it to the owner (note: it lives on n8n's network, "
        "so it's callable externally only if the deployment exposes n8n's /webhook). "
        "Add duration_days for time-boxed tracking ('for a month' → 30). "
        "LIFECYCLE: action='list' returns every watcher with its id; pause/resume/delete "
        "take that automation_id. Returns the created/affected automation as JSON (with "
        "webhook_url for webhooks), the full list for 'list', or an actionable error to "
        "fix and retry (missing url, n8n not configured, etc.)."
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
                tail = (
                    "\nConfirm to the owner in one sentence what you're now watching and until when."
                )
                if result.get("webhook_url"):
                    tail = (
                        f"\nGive the owner this inbound webhook URL so their system can POST to it: "
                        f"{result['webhook_url']} — it fires the watcher on each call. (It's on n8n's "
                        f"network; it's reachable from outside only if n8n's /webhook is exposed publicly.)"
                    )
                return (
                    "Automation created and live in n8n:\n"
                    + json.dumps(result, indent=2)
                    + tail
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
