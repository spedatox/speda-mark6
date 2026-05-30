import logging

from app.skills.base import Skill
from app.core.context import AgentContext

logger = logging.getLogger(__name__)


class NotificationsSkill(Skill):
    name = "send_push_notification"
    description = (
        "Delivers a push notification to the user's Flutter app on their Android device. "
        "Use this when output_mode is 'push' or when SPEDA determines a background result "
        "is worth surfacing to the user immediately without waiting for them to open the app. "
        "Do not use this for output_mode 'silent' — silent results are stored in DB only. "
        "Returns 'delivered' on success or an error message describing the failure."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Notification title (short, ≤64 chars)."},
            "body": {"type": "string", "description": "Notification body text."},
            "priority": {
                "type": "string",
                "enum": ["low", "normal", "high"],
                "default": "normal",
            },
        },
        "required": ["title", "body"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        # TODO: Integrate Flutter push notification delivery (FCM or similar).
        logger.info(
            "notification_execute",
            extra={"request_id": context.request_id, "title": args.get("title")},
        )
        return "Push notification delivery not yet configured."
