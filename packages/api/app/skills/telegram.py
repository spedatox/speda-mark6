"""
Telegram delivery skill (Tier 1) — an agent deliberately pushing a message or a
generated file to the owner's Telegram, mid-turn, from ITS OWN bot.

Not read-only (it sends). The sending bot is resolved from context.agent_id via
the TelegramBotRegistry's fallback chain — the agent never names a bot. Both
tools degrade to a clear tool-result string when Telegram isn't configured or the
owner hasn't linked, never an exception.
"""

import logging

from app.core.context import AgentContext
from app.core.files import safe_output_path
from app.skills.base import Skill

logger = logging.getLogger(__name__)


class SendTelegramMessageSkill(Skill):
    name = "send_telegram_message"
    read_only = False
    requires_network = True  # Bot API call — dead in a dead zone
    description = (
        "Sends a short text message to the owner's Telegram, from THIS agent's own "
        "Telegram bot, right now. Use it when the owner explicitly asks to be pinged "
        "on Telegram ('text me when the export is done', 'send that to my phone') or "
        "when you finish a background job worth surfacing immediately without waiting "
        "for them to open the app. Do NOT use it to answer the current Telegram "
        "conversation (your normal reply already goes there), for output_mode "
        "'silent' results, or to dump long content — keep it to a concise heads-up. "
        "Returns 'delivered' on success or a short reason it couldn't send."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "The message body. Keep it concise — a notification, not an essay.",
            },
        },
        "required": ["text"],
    }

    def __init__(self, bots) -> None:
        self._bots = bots  # TelegramBotRegistry

    async def execute(self, args: dict, context: AgentContext) -> str:
        text = (args.get("text") or "").strip()
        if not text:
            return "No text provided — nothing to send."
        if not self._bots.configured:
            return "Telegram isn't configured on this deployment — no bot token is set."
        ok = await self._bots.deliver_message(context.agent_id, text)
        logger.info(
            "telegram_skill_message",
            extra={"request_id": context.request_id, "agent_id": context.agent_id, "delivered": ok},
        )
        return (
            "Delivered the message to the owner's Telegram."
            if ok
            else "Couldn't deliver to Telegram — the owner may not have linked the bot yet."
        )


class SendTelegramFileSkill(Skill):
    name = "send_telegram_file"
    read_only = False
    requires_network = True
    description = (
        "Sends a file you already produced this turn (a PDF, DOCX, PPTX, image, or "
        "any saved file in the outputs directory) to the owner's Telegram, from THIS "
        "agent's own bot. This is the 'generate a deck and send it to my phone' path: "
        "call generate_document or save_file first, then pass that file's name here. "
        "Do NOT use it for files that don't exist yet, for arbitrary filesystem paths "
        "(only names inside the outputs directory resolve), or as a substitute for the "
        "download card in the desktop app. Returns confirmation of delivery or a short "
        "failure reason."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": (
                    "The name of a file already produced this turn (as returned by "
                    "generate_document / save_file), e.g. 'briefing.pdf'."
                ),
            },
            "caption": {
                "type": "string",
                "description": "Optional caption shown with the file. Defaults to the filename.",
            },
        },
        "required": ["filename"],
    }

    def __init__(self, bots) -> None:
        self._bots = bots

    async def execute(self, args: dict, context: AgentContext) -> str:
        filename = (args.get("filename") or "").strip()
        caption = (args.get("caption") or "").strip()
        if not filename:
            return "No filename provided — nothing to send."
        if not self._bots.configured:
            return "Telegram isn't configured on this deployment — no bot token is set."
        path = safe_output_path(filename)
        if path is None:
            return (
                f"Couldn't find '{filename}' in the outputs directory. Generate or "
                "save the file first, then send it by the exact name that was returned."
            )
        ok = await self._bots.deliver_document(context.agent_id, str(path), caption=caption)
        logger.info(
            "telegram_skill_file",
            extra={"request_id": context.request_id, "agent_id": context.agent_id, "delivered": ok},
        )
        return (
            f"Delivered '{filename}' to the owner's Telegram."
            if ok
            else "Couldn't deliver the file to Telegram — the owner may not have linked the bot yet."
        )
