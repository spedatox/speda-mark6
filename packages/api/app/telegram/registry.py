"""
TelegramBotRegistry — the only entity that knows which bot belongs to which
agent. Built once in the lifespan handler from the per-agent tokens in config.

Delivery goes through `bot_for(agent_id)`, which applies the fallback chain:
the agent's own started bot → SPEDA's bot (message prefixed so attribution
survives) → None (the caller logs and stores a Notification row instead). A
missing token or an unpaired bot degrades one agent's voice; it never drops a
message and never takes down the channel.
"""

import asyncio
import logging

from app.config import settings
from app.core.runtime_state import get_telegram_started
from app.telegram.client import TelegramBot

logger = logging.getLogger(__name__)

# agent_id → config attribute holding its bot token. Only agents with a
# Telegram presence appear here; the profile still gates via telegram_enabled.
_TOKEN_ATTRS: dict[str, str] = {
    "speda": "telegram_bot_token_speda",
    "sentinel": "telegram_bot_token_sentinel",
    "nightcrawler": "telegram_bot_token_nightcrawler",
    "ultron": "telegram_bot_token_ultron",
    "centurion": "telegram_bot_token_centurion",
    "atomix": "telegram_bot_token_atomix",
    "orion": "telegram_bot_token_orion",
    "optimus": "telegram_bot_token_optimus",
}

# Human tag prepended when an agent's message rides SPEDA's bot (fallback), so
# the owner still sees who it's from.
_TAG = {
    "sentinel": "🛡️ Sentinel",
    "nightcrawler": "🕸️ NightCrawler",
    "ultron": "📚 Ultron",
    "centurion": "🔐 Centurion",
    "atomix": "❤️ Atomix",
    "orion": "🛰️ Orion",
    "optimus": "⚙️ Optimus",
}


class TelegramBotRegistry:
    def __init__(self) -> None:
        self._bots: dict[str, TelegramBot] = {}

    # ── Construction ──────────────────────────────────────────────────────────

    @classmethod
    def from_config(cls, agent_ids: set[str]) -> "TelegramBotRegistry":
        """Build one TelegramBot per agent that has both a Telegram presence
        (agent_ids — profiles with telegram_enabled) and a configured token.
        SPEDA absorbs the legacy telegram_bot_token when its own is unset."""
        reg = cls()
        for agent_id in sorted(agent_ids):
            attr = _TOKEN_ATTRS.get(agent_id)
            token = getattr(settings, attr, "") if attr else ""
            if agent_id == "speda" and not token:
                token = settings.telegram_bot_token  # legacy single-bot alias
            if token:
                reg._bots[agent_id] = TelegramBot(agent_id, token)
        logger.info("telegram_registry_built", extra={"bots": sorted(reg._bots)})
        return reg

    # ── Lookup / fallback chain ───────────────────────────────────────────────

    @property
    def configured(self) -> bool:
        """Whether ANY bot is configured — drives the connect UI's enabled state."""
        return bool(self._bots)

    def has_own_bot(self, agent_id: str) -> bool:
        return agent_id in self._bots

    def get(self, agent_id: str) -> TelegramBot | None:
        """The agent's OWN bot, or None. Used by ingress (a webhook/poll is bound
        to a specific bot) — never the fallback chain."""
        return self._bots.get(agent_id)

    def all_bots(self) -> dict[str, TelegramBot]:
        return dict(self._bots)

    def resolve(self, agent_id: str) -> tuple[TelegramBot | None, str]:
        """Resolve the bot that should DELIVER for `agent_id`, plus a prefix.

        - The agent's own bot, if configured and the owner has started it → ('', own bot).
        - Otherwise SPEDA's bot with a '[Agent] ' tag → so attribution survives.
        - Otherwise (None, '') → caller stores a Notification row and logs.
        """
        started = get_telegram_started()
        own = self._bots.get(agent_id)
        if own is not None and (agent_id in started or not started):
            # If nothing is marked started yet (fresh install, legacy connect),
            # optimistically try the own bot — Telegram will simply reject if the
            # owner never started it, and the caller degrades on the False return.
            return own, ""
        speda = self._bots.get("speda")
        if speda is not None:
            tag = _TAG.get(agent_id)
            return speda, (f"{tag}\n" if tag else "")
        return None, ""

    # ── Delivery entrypoint used by push routing + the skill ──────────────────

    async def deliver_message(self, agent_id: str, text: str) -> bool:
        """Send `text` on behalf of `agent_id` via the fallback chain. Returns
        True if any bot accepted it."""
        bot, prefix = self.resolve(agent_id)
        if bot is None:
            logger.warning("telegram_no_bot_for_delivery", extra={"agent_id": agent_id})
            return False
        return await bot.send_message(text, prefix=prefix)

    async def deliver_document(self, agent_id: str, path: str, caption: str = "") -> bool:
        bot, prefix = self.resolve(agent_id)
        if bot is None:
            logger.warning("telegram_no_bot_for_document", extra={"agent_id": agent_id})
            return False
        cap = f"{prefix}{caption}" if prefix else caption
        return await bot.send_document(path, caption=cap)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self, gateway) -> list[asyncio.Task]:
        """Bring the channel online per settings.telegram_mode. Returns the set
        of long-running poll tasks (empty in webhook/off mode) so the caller can
        cancel them on shutdown. `gateway` is the TelegramGateway that turns an
        update into an orchestrator run."""
        mode = settings.telegram_mode.strip().lower()
        if mode == "webhook":
            base = settings.telegram_webhook_base.rstrip("/")
            secret = settings.telegram_webhook_secret
            if not base or not secret:
                logger.error("telegram_webhook_misconfigured")
                return []
            for agent_id, bot in self._bots.items():
                url = f"{base}/telegram/webhook/{agent_id}"
                ok = await bot.set_webhook(url, secret)
                logger.info(
                    "telegram_webhook_set" if ok else "telegram_webhook_set_failed",
                    extra={"agent_id": agent_id},
                )
            return []
        if mode == "polling":
            tasks = [
                asyncio.create_task(self._poll(agent_id, bot, gateway))
                for agent_id, bot in self._bots.items()
            ]
            logger.info("telegram_polling_started", extra={"bots": sorted(self._bots)})
            return tasks
        # off — outbound-only (delivery still works if tokens are set).
        return []

    async def _poll(self, agent_id: str, bot: TelegramBot, gateway) -> None:
        """One long-poll loop per bot (dev ingress). Feeds each update to the
        gateway. Watermark persists across restarts via runtime_state."""
        from app.core.runtime_state import get_telegram_update_offset, set_telegram_update_offset

        offset = get_telegram_update_offset(agent_id)
        while True:
            try:
                updates = await bot.get_updates(offset, timeout=25)
                for upd in updates:
                    offset = upd.get("update_id", 0) + 1
                    set_telegram_update_offset(agent_id, offset)
                    asyncio.create_task(gateway.handle_update(agent_id, upd))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.error("telegram_poll_error", extra={"agent_id": agent_id, "error": str(exc)})
                await asyncio.sleep(3)

    async def aclose(self) -> None:
        for bot in self._bots.values():
            await bot.aclose()
