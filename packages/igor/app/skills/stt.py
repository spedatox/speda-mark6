import logging
from pathlib import Path

from app.core.context import AgentContext
from app.services import stt
from app.skills.base import Skill

logger = logging.getLogger(__name__)


class STTSkill(Skill):
    name = "speech_to_text"
    deferred = True
    search_keywords = "stt transcribe transcription audio voice dictation listen recognize"
    description = (
        "Transcribes a recorded audio file to text using Azure Speech recognition, the same "
        "engine and credential that produces spoken replies. Use this when the owner uploads or "
        "points at an audio file and wants its contents in writing — a voice memo, a recorded "
        "meeting, a dictated note. Do NOT use this for the microphone in voice mode: that audio "
        "is recognised by the client before a turn even starts, so calling this tool there would "
        "transcribe something that is already text. Accepts WAV and OGG/Opus up to 60 seconds and "
        "returns the transcribed text, or an explicit note when the recording was silent."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "audio_path": {
                "type": "string",
                "description": "Absolute path to the audio file to transcribe.",
            },
            "language": {
                "type": "string",
                "description": (
                    "BCP-47 locale of the SPOKEN language, e.g. 'tr-TR' or 'en-US'. Azure decodes "
                    "the language it is told and returns confident nonsense when told the wrong "
                    "one, so name it when known. Omit to use the configured default."
                ),
            },
        },
        "required": ["audio_path"],
    }
    requires_network = True

    async def execute(self, args: dict, context: AgentContext) -> str:
        raw_path = (args.get("audio_path") or "").strip()
        if not raw_path:
            return "No audio file was supplied to transcribe."

        path = Path(raw_path)
        if not path.is_file():
            return f"No such audio file: {raw_path}"

        audio = path.read_bytes()
        content_type = stt.sniff_content_type(audio)
        if content_type is None:
            # Named explicitly rather than handed to Azure to reject: the
            # upstream 400 for an unsupported container is indistinguishable
            # from the one for a bad credential.
            return (
                f"'{path.name}' is not a container Azure Speech reads directly. "
                "Supply WAV (PCM) or OGG/Opus."
            )

        try:
            text = await stt.transcribe(audio, args.get("language"), content_type)
        except stt.STTError as exc:
            logger.warning(
                "stt_skill_failed",
                extra={"request_id": context.request_id, "error": str(exc)},
            )
            return f"Could not transcribe the audio: {exc}"

        if not text:
            # Distinguished from a failure on purpose — the file was read fine,
            # there was simply nothing said in it.
            return f"'{path.name}' contains no recognisable speech."

        logger.info(
            "stt_skill_transcribed",
            extra={"request_id": context.request_id, "chars": len(text)},
        )
        return text
