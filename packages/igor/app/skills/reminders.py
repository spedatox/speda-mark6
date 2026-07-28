import logging

from app.core.context import AgentContext
from app.services import reminders as reminder_service
from app.skills.base import Skill

logger = logging.getLogger(__name__)


class RemindersSkill(Skill):
    name = "reminders"
    description = (
        "Reads and closes the owner's PERSISTENT reminders — the ones that keep "
        "re-asking over Telegram until they are answered (medicine, exercise, "
        "chores). Use it with action 'list' to see what is currently waiting, and "
        "with action 'answer' the moment the owner tells you in chat that they did "
        "the thing ('aldım', 'took it', 'done', 'skipped it') — otherwise the "
        "reminder keeps buzzing them every few minutes until it gives up. Do NOT "
        "use it to create reminders or change their schedule (those are configured "
        "in the n8n workflow), and do NOT answer on the owner's behalf unless they "
        "actually said so. Returns the reminder that was closed and how many times "
        "it had asked, or the list of open questions."
    )
    read_only = False
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "answer"],
                "description": "'list' to see open reminders, 'answer' to close one.",
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
                    "Optional. The specific reminder to close (from 'list'). Omit to "
                    "close the newest open question, which is what the owner is "
                    "almost always replying to."
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
