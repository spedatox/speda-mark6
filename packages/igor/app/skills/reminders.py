import logging

from app.core.context import AgentContext
from app.services import reminders as reminder_service
from app.skills.base import Skill

logger = logging.getLogger(__name__)


class RemindersSkill(Skill):
    name = "reminders"
    description = (
        "Sends, reads and closes PERSISTENT reminders — questions that keep "
        "re-asking over Telegram every few minutes until the owner actually "
        "answers, then stop. Use action 'ask' when you have composed a reminder "
        "the owner must confirm (medication, supplements, packing a bag) and it "
        "would be bad for them to simply miss it; the text you pass is re-sent "
        "verbatim on every retry, so write it complete and self-contained. Use "
        "'list' to see what is still waiting, and 'answer' the moment they tell "
        "you in chat that they did it ('aldım', 'took it', 'done', 'skipped') — "
        "otherwise it keeps buzzing until it gives up. Do NOT use 'ask' for "
        "ordinary notifications that need no confirmation (use "
        "send_telegram_message), and never answer on the owner's behalf unless "
        "they actually said so. Returns the reminder that was sent or closed, "
        "with its ask count."
    )
    read_only = False
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["ask", "list", "answer"],
                "description": "'ask' to send a reminder that nags until answered, 'list' to see open ones, 'answer' to close one.",
            },
            "text": {
                "type": "string",
                "description": (
                    "For action 'ask': the full message, sent verbatim on every "
                    "retry. Include everything the owner needs — it is re-sent as-is."
                ),
            },
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "For action 'ask': the answer buttons, e.g. ['✅ Aldım', "
                    "'⏭️ Atladım']. Put the expected answer first. Defaults to a "
                    "single done button."
                ),
            },
            "every_minutes": {
                "type": "integer",
                "description": "For action 'ask': minutes between retries (default 5).",
            },
            "max_asks": {
                "type": "integer",
                "description": (
                    "For action 'ask': how many times to ask before giving up and "
                    "logging it missed (default 10)."
                ),
            },
            "answer": {
                "type": "string",
                "description": (
                    "For action 'answer': what the owner actually did — 'taken', "
                    "'done', 'skipped'. Defaults to 'done'."
                ),
            },
            "reminder_id": {
                "type": "string",
                "description": (
                    "Optional. For 'answer', the specific reminder to close (from "
                    "'list'); omit to close the newest open question, which is what "
                    "the owner is almost always replying to. For 'ask', a stable id "
                    "like 'atomix_evening' — re-using it while one is open refuses "
                    "to send a duplicate."
                ),
            },
        },
        "required": ["action"],
    }

    def __init__(self, bots) -> None:
        # TelegramBotRegistry, injected like the other Telegram-touching skills —
        # answering clears the buttons on the message that asked, which needs the
        # bot that sent it.
        self._bots = bots

    async def execute(self, args: dict, context: AgentContext) -> str:
        action = (args.get("action") or "list").strip().lower()
        bots = self._bots

        if action == "ask":
            text = (args.get("text") or "").strip()
            if not text:
                return "Nothing sent: 'ask' needs the reminder text."
            result = await reminder_service.open_ask(
                context.db,
                agent_id=context.agent_id,
                # Scoped per agent and per day so a retried trigger cannot open a
                # second competing question for the same evening.
                reminder_id=(args.get("reminder_id") or f"{context.agent_id}_ask").strip(),
                text=text,
                options=args.get("options"),
                every_minutes=int(args.get("every_minutes") or 5),
                max_asks=int(args.get("max_asks") or 10),
                bots=bots,
            )
            status = result.get("status")
            if status == "already_open":
                return (
                    f"Not sent — '{result['reminder_id']}' is already waiting for an "
                    f"answer (asked {result.get('asks')} time(s)). Do not send a duplicate."
                )
            if status != "ok":
                return f"Could not send the reminder: {result.get('detail', status)}"
            logger.info(
                "reminder_opened_by_agent",
                extra={"request_id": context.request_id,
                       "reminder_id": result.get("reminder_id")},
            )
            return (
                f"Reminder sent to the owner's Telegram with answer buttons. It will "
                f"re-ask every {result['every_minutes']} min until they answer, up to "
                f"{result['max_asks']} times. Do not also send this as a normal message."
            )

        if action == "list":
            open_rows = await reminder_service.list_open(context.db)
            if not open_rows:
                return "Nothing is waiting for an answer right now."
            lines = [
                f"- [{r['reminder_id']}] {r['question'][:120]} "
                f"(asked {r['asks']}/{r['max_asks']}, since {r['opened_at']})"
                for r in open_rows
            ]
            return f"{len(open_rows)} reminder(s) waiting:\n" + "\n".join(lines)

        result = await reminder_service.answer_latest(
            context.db,
            (args.get("answer") or "done").strip()[:64],
            reminder_id=(args.get("reminder_id") or "").strip(),
            bots=bots,
        )
        status = result.get("status")
        if status == "none_open":
            return (
                "Nothing was waiting for an answer — there is no open reminder to "
                "close. Do not claim you closed one."
            )
        if status == "already":
            return f"That reminder was already closed as '{result.get('answer')}'."
        if status != "ok":
            return f"Could not close the reminder: {result.get('detail', status)}"
        logger.info(
            "reminder_closed_by_agent",
            extra={"request_id": context.request_id,
                   "reminder_id": result.get("reminder_id")},
        )
        return (
            f"Closed '{result.get('reminder_id')}' as '{result.get('answer')}' "
            f"after {result.get('asks')} ask(s). It will stop buzzing now."
        )
