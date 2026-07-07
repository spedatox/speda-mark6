"""
One-time /start pairing per bot.

The owner taps the deep link `https://t.me/<bot>?start=<nonce>`; the `/start
<nonce>` update flows through normal ingress (webhook or poll) into the gateway,
which calls handle_start(). First link captures the owner's Telegram user id
(the same number across every bot); each subsequent bot just needs its own
Start tap so Telegram will let it message the owner.
"""

import logging

from app.core.runtime_state import (
    get_telegram_owner_id,
    mark_telegram_started,
    set_telegram_owner_id,
)

logger = logging.getLogger(__name__)

# Must match TelegramBot._CONNECT_NONCE and the deep link the UI hands out.
CONNECT_NONCE = "spedaconnect"


def validate_nonce(payload: str) -> bool:
    """The token after `/start`. Empty (a bare /start) is accepted too, so the
    owner can pair a bot just by opening it and tapping Start."""
    payload = (payload or "").strip()
    return payload in ("", CONNECT_NONCE)


def handle_start(agent_id: str, sender_id: str) -> None:
    """Record the pairing. On the very first link this also claims `sender_id` as
    the owner; afterwards it only marks this bot started. A mismatched sender on
    a system that already has an owner is ignored by the gateway's authorize step
    before this is ever reached."""
    if not get_telegram_owner_id():
        set_telegram_owner_id(sender_id)
        logger.info("telegram_owner_claimed", extra={"agent_id": agent_id})
    mark_telegram_started(agent_id)
