import logging

from app.core.context import AgentContext
from app.database import AsyncSessionLocal
from app.services import academic as academic_service
from app.services import fcm
from app.skills.base import Skill

logger = logging.getLogger(__name__)


class NotificationsSkill(Skill):
    name = "send_push_notification"
    requires_network = True  # FCM delivery — dead in a dead zone
    description = (
        "Delivers a push notification to the owner's registered devices (the Ultron Wear watch, "
        "and anything else that has registered via POST /devices/register) through Firebase Cloud "
        "Messaging. Use this when output_mode is 'push', or when a background result is worth "
        "surfacing immediately rather than waiting for the owner to open an app — a finished "
        "long-running task, a deadline that just turned urgent, an alert handed over by another "
        "agent. Do NOT use it for output_mode 'silent' (those are stored in the DB only), do NOT "
        "use it to ask about class attendance (that has its own trigger and a payload shape the "
        "watch parses differently), and do NOT use it for anything the owner has not asked to be "
        "interrupted about — this vibrates a watch on their wrist. Returns the number of devices "
        "delivered to, or a description of the failure, including 'push is not configured' when no "
        "FCM credentials are set, which is a valid deployment rather than an error."
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
                "description": (
                    "'high' wakes the device out of Doze — reserve it for genuinely "
                    "time-critical things. 'normal' lets the OS batch delivery."
                ),
            },
        },
        "required": ["title", "body"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        title = (args.get("title") or "").strip()
        body = (args.get("body") or "").strip()
        if not title or not body:
            return "Both title and body are required."

        # 'low' has no FCM equivalent; it collapses to normal, which is what the
        # OS would do with it anyway.
        priority = "high" if args.get("priority") == "high" else "normal"

        async with AsyncSessionLocal() as db:
            devices = await academic_service.active_devices(db)
            if not devices:
                return "No devices are registered, so there is nowhere to deliver this."

            delivered = 0
            failures: list[str] = []
            for device in devices:
                ok, detail = await fcm.send_data_message(
                    fid=device.fid,
                    data={"type": "notification", "title": title, "body": body},
                    priority=priority,
                )
                if ok:
                    delivered += 1
                elif detail == "unregistered":
                    # App uninstalled or its data cleared. Retiring the device
                    # here stops every future push wasting a request on it.
                    await academic_service.deactivate_device(db, device.fid)
                    failures.append(f"{device.device_id}: no longer installed (deactivated)")
                else:
                    failures.append(f"{device.device_id}: {detail}")

        logger.info(
            "notification_execute",
            extra={
                "request_id": context.request_id,
                "title": title,
                "delivered": delivered,
                "failed": len(failures),
            },
        )

        if delivered and not failures:
            return f"Delivered to {delivered} device(s)."
        if delivered:
            return f"Delivered to {delivered} device(s). Failures: {'; '.join(failures)}"
        return f"Delivery failed. {'; '.join(failures)}"
