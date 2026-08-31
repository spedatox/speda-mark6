# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

import logging
import uuid
from pathlib import Path

from app.config import settings
from app.core.context import AgentContext
from app.services import tts
from app.skills.base import Skill

logger = logging.getLogger(__name__)


class TTSSkill(Skill):
    name = "text_to_speech"
    deferred = True
    search_keywords = "tts speak voice audio say aloud speech synthesis"
    description = (
        "Converts text into spoken audio using Azure Speech neural voices and returns it as a "
        "downloadable MP3 file. Use this when the owner explicitly asks for something to be read "
        "aloud, recorded, or delivered as audio they can keep — a voice note, a spoken summary, a "
        "pronunciation. Do NOT use this to answer normally in voice mode: there, every reply is "
        "spoken automatically by the client and calling this tool would produce a redundant second "
        "recording. Returns a confirmation naming the generated file, which the owner receives as a "
        "download card; markdown, code blocks and tables are stripped before synthesis because they "
        "are unlistenable."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The text to synthesise into speech."},
            "voice": {
                "type": "string",
                "description": (
                    "Azure voice name, e.g. 'tr-TR-EmelNeural' (female) or 'tr-TR-AhmetNeural' "
                    "(male). Omit to use the speaking agent's own configured voice."
                ),
            },
            "title": {
                "type": "string",
                "description": "Short label for the download card, e.g. 'Morning brief'.",
            },
        },
        "required": ["text"],
    }
    requires_network = True

    async def execute(self, args: dict, context: AgentContext) -> str:
        text = (args.get("text") or "").strip()
        if not text:
            return "No text was supplied to synthesise."

        voice = args.get("voice") or context.extra.get("voice_id") or None
        try:
            # Not a live streaming path — one file, generated once — so the
            # extra LLM sanitize pass in tts.synthesize costs a second or two
            # of latency the owner is already waiting through, not a per-
            # sentence tax. Uses the turn's own model (Rule 10: resolved by
            # the caller, never hardcoded here).
            audio = await tts.synthesize(text, voice, sanitize_model=context.model)
        except tts.TTSError as exc:
            logger.warning(
                "tts_skill_failed",
                extra={"request_id": context.request_id, "error": str(exc)},
            )
            return f"Could not generate audio: {exc}"

        title = (args.get("title") or "Speech").strip()
        safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in title).strip() or "speech"
        name = f"{uuid.uuid4().hex[:8]}_{safe.replace(' ', '_')}.mp3"
        path = Path(settings.temp_outputs_dir) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(audio)

        from app.core.files import register_file

        register_file(context, str(path), title=title)
        logger.info(
            "tts_skill_generated",
            extra={"request_id": context.request_id, "file": name, "bytes": len(audio)},
        )
        return f"Generated the audio '{title}' ({len(audio) // 1024} KB). It is ready to download."
