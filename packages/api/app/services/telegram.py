"""
Telegram delivery — SPEDA's outbound voice for proactive notifications.

When an n8n watcher fires, the orchestrator composes a message and this client
pushes it to the owner's Telegram chat. Linking is one-time via the in-app
"Connect Telegram" deep link: the owner taps Start, the bot receives
`/start <nonce>`, and we capture their chat id (no public webhook needed — we
poll getUpdates during the connect window).
"""

import asyncio
import logging

import httpx

from app.config import settings
from app.core.runtime_state import get_telegram_chat_id, set_telegram_chat_id

logger = logging.getLogger(__name__)

_CONNECT_NONCE = "spedaconnect"


class TelegramClient:
    """Thin wrapper over the Telegram Bot API. Injected on app.state at startup."""

    def __init__(self) -> None:
        self._token = settings.telegram_bot_token

    @property
    def configured(self) -> bool:
        return bool(self._token)

    def _url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self._token}/{method}"

    async def _call(self, method: str, **params) -> dict | None:
        if not self.configured:
            logger.warning("telegram_not_configured", extra={"method": method})
            return None
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(self._url(method), json=params)
                resp.raise_for_status()
                data = resp.json()
                return data.get("result") if data.get("ok") else None
        except Exception as exc:  # noqa: BLE001
            logger.error("telegram_call_failed", extra={"method": method, "error": str(exc)})
            return None

    async def get_username(self) -> str | None:
        """Bot @username — used to build the connect deep link."""
        me = await self._call("getMe")
        return me.get("username") if me else None

    async def send_message(self, text: str, chat_id: str | None = None) -> bool:
        """Deliver a message to the owner. Uses the connected chat id by default.
        Returns True on success. Markdown is best-effort — falls back to plain on
        a formatting error so a stray underscore never drops the message."""
        chat = chat_id or get_telegram_chat_id()
        if not chat:
            logger.warning("telegram_no_chat_id")
            return False
        result = await self._call(
            "sendMessage", chat_id=chat, text=text,
            parse_mode="Markdown", disable_web_page_preview=False,
        )
        if result is None:
            # Retry once without Markdown in case the body broke the parser.
            result = await self._call("sendMessage", chat_id=chat, text=text)
        return result is not None

    async def connect_deep_link(self) -> str | None:
        """`https://t.me/<bot>?start=<nonce>` — the 'Connect Telegram' target."""
        username = await self.get_username()
        if not username:
            return None
        return f"https://t.me/{username}?start={_CONNECT_NONCE}"

    async def capture_chat_id(self, timeout_s: int = 90) -> str | None:
        """Poll getUpdates for the owner's `/start <nonce>` and persist their chat
        id. Called after the owner is handed the deep link. Returns the chat id on
        success, or None if they didn't tap Start within the window."""
        if not self.configured:
            return None
        deadline = asyncio.get_event_loop().time() + timeout_s
        offset = 0
        while asyncio.get_event_loop().time() < deadline:
            updates = await self._call("getUpdates", offset=offset, timeout=20) or []
            for upd in updates:
                offset = max(offset, upd.get("update_id", 0) + 1)
                msg = upd.get("message") or {}
                text = msg.get("text", "")
                if text.startswith("/start"):
                    chat_id = str(msg.get("chat", {}).get("id", ""))
                    if chat_id:
                        set_telegram_chat_id(chat_id)
                        await self.send_message(
                            "✅ Connected. SPEDA will reach you here when something "
                            "you're watching for happens.",
                            chat_id=chat_id,
                        )
                        logger.info("telegram_connected", extra={"chat_id": chat_id})
                        return chat_id
            await asyncio.sleep(1)
        return None
