"""
Renderer — turns an orchestrator SSEEvent stream into Telegram messages.

A peer of the SSE serializer (routers/chat.py) and the WS to_json() path: same
events, different transport. v1 buffers text and flushes it at DONE (no live
message editing — Telegram's edit rate limits make streaming a poor trade on
mobile; see decision TG-7). Files are delivered as they arrive, BEFORE the final
text, so a generated deck lands with its explanation following it.
"""

import asyncio
import logging
from pathlib import Path

from app.config import settings
from app.core.files import safe_output_path
from app.schemas.sse import SSEEventType
from app.telegram.client import TelegramBot

logger = logging.getLogger(__name__)


async def render_stream(engine, bot: TelegramBot, chat_id: str, request_id: str) -> str:
    """Drive `engine` (an async SSEEvent generator) to completion, delivering to
    the owner's chat via `bot`. Returns the assembled assistant text so the
    caller can persist it to the session transcript."""
    chunks: list[str] = []
    typing_task = asyncio.create_task(_keep_typing(bot, chat_id))
    try:
        async for event in engine:
            et = event.type
            if et == SSEEventType.CHUNK and isinstance(event.data, str):
                chunks.append(event.data)
            elif et == SSEEventType.FILE:
                await _deliver_file(bot, chat_id, event.data)
            elif et == SSEEventType.ERROR:
                await bot.send_message(
                    f"⚠️ Something went wrong handling that (ref {request_id[:8]}). "
                    "Try again in a moment.",
                    chat_id=chat_id,
                )
                logger.error("telegram_render_error", extra={"request_id": request_id, "error": str(event.data)})
    finally:
        typing_task.cancel()

    text = "".join(chunks).strip()
    if text:
        await bot.send_message(text, chat_id=chat_id)
    return text


async def _keep_typing(bot: TelegramBot, chat_id: str) -> None:
    """Re-assert the typing indicator every ~4 s while the turn runs (Telegram
    clears it after ~5 s). Cancelled when the stream ends."""
    try:
        while True:
            await bot.send_chat_action(chat_id=chat_id, action="typing")
            await asyncio.sleep(4)
    except asyncio.CancelledError:
        return
    except Exception:  # noqa: BLE001
        return


async def _deliver_file(bot: TelegramBot, chat_id: str, meta) -> None:
    """A FILE event carries the produced-file metadata dict (see core/files.py).
    Resolve it inside temp_outputs_dir (jail) and upload it."""
    if not isinstance(meta, dict):
        return
    name = meta.get("name") or ""
    path = safe_output_path(name)
    if path is None:
        # Fall back to the raw temp dir join if the name didn't resolve.
        candidate = Path(settings.temp_outputs_dir) / Path(name).name
        path = candidate if candidate.is_file() else None
    if path is None:
        logger.warning("telegram_file_unresolved", extra={"name": name})
        return
    caption = meta.get("title") or name
    await bot.send_document(str(path), caption=caption, chat_id=chat_id)
