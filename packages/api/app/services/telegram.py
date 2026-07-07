"""
DEPRECATED — the single-bot TelegramClient has been ABSORBED into the per-agent
Telegram package (docs/TELEGRAM_ARCHITECTURE.md, TG-10). There is now one client
class, app.telegram.client.TelegramBot, and one owner of the fleet,
app.telegram.registry.TelegramBotRegistry (on app.state.telegram_bots).

This module remains only as a backward-compatible alias so any lingering import
keeps resolving. New code must use the registry, never construct a client here.
"""

from app.telegram.client import TelegramBot

# Legacy name. The old TelegramClient took no args and read the single
# TELEGRAM_BOT_TOKEN; the shim binds that to the SPEDA agent id.
class TelegramClient(TelegramBot):  # noqa: D401 - compat shim
    def __init__(self) -> None:
        from app.config import settings

        super().__init__("speda", settings.telegram_bot_token)


__all__ = ["TelegramClient", "TelegramBot"]
