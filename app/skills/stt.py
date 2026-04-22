import logging

from app.skills.base import Skill
from app.core.context import AgentContext

logger = logging.getLogger(__name__)


class STTSkill(Skill):
    name = "speech_to_text"
    description = (
        "Transcribes an audio file to text using the local Whisper STT engine running on Contabo. "
        "Use this when processing a voice message from the user or transcribing an uploaded audio file. "
        "Do not use this for text inputs that do not contain audio. "
        "Returns the transcribed text as a plain string."
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
                "description": "ISO 639-1 language code. Defaults to auto-detect.",
                "default": "auto",
            },
        },
        "required": ["audio_path"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        # TODO: Integrate Whisper STT engine once deployed on Contabo.
        logger.info("stt_execute", extra={"request_id": context.request_id})
        return "STT not yet configured. Whisper STT integration pending deployment."
