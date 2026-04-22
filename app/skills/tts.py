import logging

from app.skills.base import Skill
from app.core.context import AgentContext

logger = logging.getLogger(__name__)


class TTSSkill(Skill):
    name = "text_to_speech"
    description = (
        "Converts text to speech audio using the local Kokoro TTS engine running on Contabo. "
        "Use this when the user requests a spoken response, audio playback, or voice output. "
        "Do not use this for silent background tasks or when output_mode is 'push' or 'silent'. "
        "Returns a file path to the generated .wav audio file in /tmp/speda_outputs/."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The text to synthesise into speech."},
            "voice": {
                "type": "string",
                "description": "Voice identifier for Kokoro TTS.",
                "default": "default",
            },
        },
        "required": ["text"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        # TODO: Integrate Kokoro TTS engine once deployed on Contabo.
        logger.info("tts_execute", extra={"request_id": context.request_id})
        return "TTS not yet configured. Kokoro TTS integration pending deployment."
