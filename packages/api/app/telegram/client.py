"""
TelegramBot — a thin, reliable wrapper over one bot's Bot API token.

One instance per configured token (one per agent). It owns nothing about
identity: it is a dumb pipe that sends text/files and (in polling mode) reads
updates. Which agent a bot belongs to is the registry's concern, not this
class's. Supersedes the old single-bot services/telegram.TelegramClient — the
legacy connect helpers (configured / get_username / connect_deep_link /
capture_chat_id) live on here so the existing "Connect Telegram" UI keeps
working against SPEDA's bot.
"""

import asyncio
import logging
import re
from pathlib import Path

import httpx

from app.core.runtime_state import (
    get_telegram_owner_id,
    mark_telegram_started,
    set_telegram_owner_id,
)

logger = logging.getLogger(__name__)

_API = "https://api.telegram.org"
# Telegram hard limits.
_MAX_TEXT = 4096
_MAX_FILE_BYTES = 50 * 1024 * 1024
_CONNECT_NONCE = "spedaconnect"


def _split_text(text: str, limit: int = _MAX_TEXT) -> list[str]:
    """Chunk a long message under Telegram's 4096-char ceiling, preferring
    paragraph then line then hard-cut boundaries so formatting survives."""
    text = text or ""
    if len(text) <= limit:
        return [text] if text else []
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = window.rfind("\n\n")
        if cut < limit // 2:
            cut = window.rfind("\n")
        if cut < limit // 2:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = limit
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    if remaining:
        chunks.append(remaining)
    return chunks


def _md_to_html(text: str) -> str:
    """Best-effort Markdown → Telegram-HTML. Telegram's HTML parse mode is far
    more forgiving than its Markdown mode (a stray '_' or '*' won't drop the
    message), so we escape the text and re-apply a safe subset of formatting.
    On any doubt the caller falls back to plain text, so this only needs to
    handle the common cases the agents actually emit."""
    # Escape HTML-significant chars first.
    out = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # Fenced/inline code → <pre>/<code> (before inline emphasis so * inside code
    # isn't mangled).
    out = re.sub(r"```(?:\w+)?\n?(.*?)```", lambda m: f"<pre>{m.group(1)}</pre>", out, flags=re.DOTALL)
    out = re.sub(r"`([^`\n]+)`", lambda m: f"<code>{m.group(1)}</code>", out)
    # **bold** and __bold__
    out = re.sub(r"\*\*([^*\n]+)\*\*", lambda m: f"<b>{m.group(1)}</b>", out)
    # *italic* / _italic_ (single, not touching bold which we already consumed)
    out = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", lambda m: f"<i>{m.group(1)}</i>", out)
    # [text](url) links
    out = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', out)
    # Markdown headings/bullets have no HTML equivalent — leave the text, just
    # drop the leading hashes so they don't shout.
    out = re.sub(r"(?m)^#{1,6}\s*", "", out)
    return out


class TelegramBot:
    """Bot API client for a single token. Reuses one httpx client across calls."""

    def __init__(self, agent_id: str, token: str) -> None:
        self.agent_id = agent_id
        self._token = token
        self._client: httpx.AsyncClient | None = None

    @property
    def configured(self) -> bool:
        return bool(self._token)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _url(self, method: str) -> str:
        return f"{_API}/bot{self._token}/{method}"

    async def _call(self, method: str, **params) -> dict | None:
        """POST a JSON Bot API call. Honors a single 429 retry_after. Returns the
        `result` payload on success, None on any failure (never raises — a failed
        send must degrade, per the fallback chain, not crash a turn)."""
        if not self.configured:
            logger.warning("telegram_not_configured", extra={"agent_id": self.agent_id, "method": method})
            return None
        for attempt in (1, 2):
            try:
                resp = await self._http().post(self._url(method), json=params)
                if resp.status_code == 429 and attempt == 1:
                    retry_after = resp.json().get("parameters", {}).get("retry_after", 1)
                    await asyncio.sleep(min(retry_after, 5))
                    continue
                resp.raise_for_status()
                data = resp.json()
                return data.get("result") if data.get("ok") else None
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "telegram_call_failed",
                    extra={"agent_id": self.agent_id, "method": method, "error": str(exc)},
                )
                return None
        return None

    # ── Outbound ────────────────────────────────────────────────────────────

    async def send_message(self, text: str, chat_id: str | None = None, prefix: str = "") -> bool:
        """Deliver text to the owner (chunked at 4096). Tries Telegram-HTML for
        formatting, falls back to plain text so a formatting glitch never drops
        the message. `prefix` tags fallback-chain sends (e.g. '[Sentinel] ')."""
        chat = chat_id or get_telegram_owner_id()
        if not chat:
            logger.warning("telegram_no_owner", extra={"agent_id": self.agent_id})
            return False
        body = f"{prefix}{text}" if prefix else text
        ok_any = False
        for chunk in _split_text(body) or [""]:
            if not chunk:
                continue
            result = await self._call(
                "sendMessage", chat_id=chat, text=_md_to_html(chunk),
                parse_mode="HTML", disable_web_page_preview=False,
            )
            if result is None:
                result = await self._call("sendMessage", chat_id=chat, text=chunk)
            ok_any = ok_any or result is not None
        return ok_any

    async def send_chat_action(self, chat_id: str | None = None, action: str = "typing") -> None:
        chat = chat_id or get_telegram_owner_id()
        if chat:
            await self._call("sendChatAction", chat_id=chat, action=action)

    async def send_document(self, path: str, caption: str = "", chat_id: str | None = None) -> bool:
        """Upload a file from disk as a document (or photo for images). Enforces
        the 50 MB Bot API ceiling with a clear log. Multipart, so it can't use
        the JSON _call path."""
        chat = chat_id or get_telegram_owner_id()
        if not chat:
            logger.warning("telegram_no_owner", extra={"agent_id": self.agent_id})
            return False
        p = Path(path)
        if not p.is_file():
            logger.error("telegram_file_missing", extra={"agent_id": self.agent_id, "path": path})
            return False
        if p.stat().st_size > _MAX_FILE_BYTES:
            logger.error(
                "telegram_file_too_large",
                extra={"agent_id": self.agent_id, "size": p.stat().st_size},
            )
            return False
        is_image = p.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp"}
        method, field = ("sendPhoto", "photo") if is_image else ("sendDocument", "document")
        data: dict = {"chat_id": chat}
        if caption:
            data["caption"] = caption[:1024]
        try:
            with p.open("rb") as fh:
                files = {field: (p.name, fh)}
                resp = await self._http().post(self._url(method), data=data, files=files)
                resp.raise_for_status()
                return bool(resp.json().get("ok"))
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "telegram_document_failed",
                extra={"agent_id": self.agent_id, "path": path, "error": str(exc)},
            )
            return False

    async def download_file(self, file_id: str, dest_dir: str) -> str | None:
        """Resolve a Telegram file_id to a temp path on disk (inbound voice/photo/
        document). Returns the local path, or None on failure."""
        info = await self._call("getFile", file_id=file_id)
        if not info or not info.get("file_path"):
            return None
        remote = info["file_path"]
        try:
            url = f"{_API}/file/bot{self._token}/{remote}"
            resp = await self._http().get(url)
            resp.raise_for_status()
            dest = Path(dest_dir) / f"tg_{file_id}_{Path(remote).name}"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(resp.content)
            return str(dest)
        except Exception as exc:  # noqa: BLE001
            logger.error("telegram_download_failed", extra={"agent_id": self.agent_id, "error": str(exc)})
            return None

    # ── Ingress plumbing ──────────────────────────────────────────────────────

    async def get_updates(self, offset: int, timeout: int = 25) -> list[dict]:
        return await self._call("getUpdates", offset=offset, timeout=timeout) or []

    async def get_username(self) -> str | None:
        me = await self._call("getMe")
        return me.get("username") if me else None

    async def set_webhook(self, url: str, secret_token: str) -> bool:
        result = await self._call(
            "setWebhook", url=url, secret_token=secret_token,
            allowed_updates=["message"], drop_pending_updates=False,
        )
        return result is not None

    async def delete_webhook(self) -> bool:
        return await self._call("deleteWebhook") is not None

    # ── Legacy connect helpers (single-bot "Connect Telegram" UI) ─────────────

    async def connect_deep_link(self) -> str | None:
        """`https://t.me/<bot>?start=<nonce>` — the connect-flow target."""
        username = await self.get_username()
        return f"https://t.me/{username}?start={_CONNECT_NONCE}" if username else None

    async def capture_chat_id(self, timeout_s: int = 90) -> str | None:
        """Legacy polling capture for the in-app connect flow (used only when
        telegram_mode='off', i.e. no persistent ingress task is running). Once
        ingress exists the /start update flows through the gateway instead."""
        if not self.configured:
            return None
        deadline = asyncio.get_event_loop().time() + timeout_s
        offset = 0
        while asyncio.get_event_loop().time() < deadline:
            for upd in await self.get_updates(offset, timeout=20):
                offset = max(offset, upd.get("update_id", 0) + 1)
                msg = upd.get("message") or {}
                if str(msg.get("text", "")).startswith("/start"):
                    owner_id = str(msg.get("from", {}).get("id") or msg.get("chat", {}).get("id", ""))
                    if owner_id:
                        set_telegram_owner_id(owner_id)
                        mark_telegram_started(self.agent_id)
                        await self.send_message(
                            "✅ Connected. I'll reach you here when something you're "
                            "watching for happens — and you can talk to me right here.",
                            chat_id=owner_id,
                        )
                        logger.info("telegram_connected", extra={"agent_id": self.agent_id})
                        return owner_id
            await asyncio.sleep(1)
        return None
