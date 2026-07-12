"""
TelegramGateway — inbound update → orchestrator run → rendered reply.

This is the ONLY module in the Telegram package with request logic (the webhook
router is dumb: it validates the secret, acks 200, and hands off here). A
Telegram turn is a NORMAL orchestrator turn — triggered_by="user",
output_mode="respond" — so persistence, memory extraction, compaction, and the
Optimus external-proxy branch all work exactly as they do for the desktop chat
router. The only differences are the transport (renderer.py) and a sticky,
channel-scoped session.
"""

import hmac
import logging
import uuid

from app.core.context import AgentContext
from app.core.runtime_state import get_telegram_owner_id
from app.database import AsyncSessionLocal
from app.telegram import linking
from app.telegram.renderer import render_stream

logger = logging.getLogger(__name__)

_OWNER_USER_ID = 1  # single-user system (CLAUDE.md)


class TelegramGateway:
    def __init__(self, orchestrator, session_manager, profiles, bots, ws_manager, agent_proxy) -> None:
        self._orchestrator = orchestrator
        self._sessions = session_manager
        self._profiles = profiles
        self._bots = bots                 # TelegramBotRegistry
        self._ws_manager = ws_manager
        self._agent_proxy = agent_proxy
        # De-dupe across webhook retries AND poll/webhook overlap: last update_id
        # seen per (agent). Poll also persists a watermark; this is the in-process
        # guard that covers the webhook path and rapid retries.
        self._seen: dict[str, int] = {}

    async def handle_update(self, agent_id: str, update: dict) -> None:
        """Entry point for one Telegram update. Runs as a background task; never
        raises to the caller (webhook must have already 200'd)."""
        try:
            await self._handle(agent_id, update)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "telegram_gateway_error",
                extra={"agent_id": agent_id, "error": str(exc)},
            )

    async def _handle(self, agent_id: str, update: dict) -> None:
        update_id = int(update.get("update_id", 0))
        # 1. Dedupe.
        if update_id and self._seen.get(agent_id, -1) >= update_id:
            return
        if update_id:
            self._seen[agent_id] = update_id

        message = update.get("message") or update.get("edited_message")
        if not message:
            return  # non-message update (we only subscribe to messages anyway)

        sender_id = str(message.get("from", {}).get("id", ""))
        chat_id = str(message.get("chat", {}).get("id", ""))
        text = (message.get("text") or message.get("caption") or "").strip()

        bot = self._bots.get(agent_id)
        if bot is None:
            logger.warning("telegram_update_unknown_bot", extra={"agent_id": agent_id})
            return

        # 2. Commands that must run BEFORE the owner check: /start pairs the bot
        # and (first time) claims the owner.
        if text.startswith("/start"):
            payload = text[len("/start"):].strip()
            owner = get_telegram_owner_id()
            if owner and not hmac.compare_digest(sender_id, owner):
                logger.warning("telegram_start_wrong_sender", extra={"agent_id": agent_id})
                return
            if not linking.validate_nonce(payload):
                return
            linking.handle_start(agent_id, sender_id)
            await bot.send_message(
                self._greeting(agent_id), chat_id=chat_id,
            )
            return

        # 3. Authorize every other update: only the owner is ever processed.
        owner = get_telegram_owner_id()
        if not owner or not hmac.compare_digest(sender_id, owner):
            logger.warning(
                "telegram_unauthorized_sender",
                extra={"agent_id": agent_id, "sender": sender_id[:6]},
            )
            return

        # 4. /new resets the sticky session.
        if text.startswith("/new"):
            async with AsyncSessionLocal() as db:
                await self._sessions.reset_channel_session(db, "telegram", agent_id)
            await bot.send_message("🆕 Started a fresh conversation.", chat_id=chat_id)
            return

        profile = self._profiles.get(agent_id)
        if profile is None:
            await bot.send_message(f"Unknown agent '{agent_id}'.", chat_id=chat_id)
            return

        # 5. Inbound media (T3): voice → STT; photo/document → attachments text.
        user_content, note = await self._build_user_content(bot, message, text)
        if note:
            await bot.send_message(note, chat_id=chat_id)
        if user_content is None:
            return  # nothing usable in the message

        # 6. Run the turn on a task-owned DB session (the request that delivered
        # this update, if any, is long gone).
        request_id = str(uuid.uuid4())
        async with AsyncSessionLocal() as db:
            session = await self._sessions.get_or_create(
                db=db,
                user_id=_OWNER_USER_ID,
                triggered_by="user",
                model_used=profile.allocate_telegram_model(),
                agent_id=agent_id,
                channel="telegram",
            )
            await self._sessions.save_message(db, session.id, "user", user_content)
            history = await self._sessions.load_history(db, session.id)

            context = AgentContext(
                agent_id=agent_id,
                user_id=_OWNER_USER_ID,
                session_id=session.id,
                request_id=request_id,
                triggered_by="user",
                trigger_payload={"message": text, "channel": "telegram"},
                output_mode="respond",
                model=profile.allocate_telegram_model(),
                system_prompt="",
                conversation_history=history,
                db=db,
                timezone="UTC",
            )
            context.extra["active_servers"] = self._sessions.get_loaded_servers(session.id)

            # Optimus (or any external_backend agent) proxies to its peer when
            # connected — same branch routers/chat.py takes. Both yield SSEEvents.
            use_external = (
                profile.external_backend
                and self._ws_manager.is_connected(profile.agent_id)
            )
            engine = (
                self._agent_proxy.run(context)
                if use_external
                else self._orchestrator.run(context)
            )

            reply = await render_stream(engine, bot, chat_id, request_id)
            if reply:
                await self._sessions.save_message(
                    db, session.id, "assistant", [{"type": "text", "text": reply}],
                )

        # 7. Background tasks — the same canonical post-turn fan-out as the
        # HTTP path (log, recap, daily maintenance, title, compaction,
        # embedding), so Telegram sessions get episodic recaps too.
        import asyncio

        from app.services.memory import run_post_turn_tasks

        bg_model = profile.background_model(profile.allocate_telegram_model())
        asyncio.create_task(
            run_post_turn_tasks(session.id, request_id, _OWNER_USER_ID, bg_model)
        )

    async def _build_user_content(self, bot, message: dict, text: str):
        """Turn an inbound Telegram message into a user-turn content payload.

        Returns (content, note): content is a str or Anthropic blocks list to
        save as the user turn, or None if unusable; note is an optional status
        line to send back (e.g. an unsupported-media apology)."""
        # Voice / audio → STT (graceful until Whisper is wired — see skills/stt).
        if message.get("voice") or message.get("audio"):
            return None, (
                "🎙️ I received a voice note, but voice transcription isn't wired up "
                "yet — send text for now."
            )

        # Photo / document → extract to text and attach alongside any caption.
        media = None
        if message.get("document"):
            media = message["document"]
        elif message.get("photo"):
            media = message["photo"][-1]  # largest size
        if media and media.get("file_id"):
            import tempfile

            path = await bot.download_file(media["file_id"], tempfile.gettempdir())
            if path:
                name = media.get("file_name") or path.split("/")[-1]
                mime = media.get("mime_type") or ""
                try:
                    from app.services.attachments import extract_text

                    with open(path, "rb") as fh:
                        import base64

                        data = base64.b64encode(fh.read()).decode()
                    extracted = extract_text(name, mime, data)
                    blocks = [{"type": "text", "text": extracted}]
                    if text:
                        blocks.append({"type": "text", "text": text})
                    return blocks, None
                except Exception as exc:  # noqa: BLE001
                    logger.error("telegram_attachment_failed", extra={"error": str(exc)})
                    return (text or "(sent a file I couldn't read)"), None

        if not text:
            return None, None
        return text, None

    def _greeting(self, agent_id: str) -> str:
        profile = self._profiles.get(agent_id)
        name = getattr(profile, "name", agent_id.title()) if profile else agent_id.title()
        domain = getattr(profile, "domain", "") if profile else ""
        tail = f" — {domain}" if domain else ""
        return (
            f"✅ Connected to {name}{tail}.\n\n"
            "Talk to me right here, and I'll reach out when something you're "
            "watching for happens. Send /new to start a fresh conversation."
        )
