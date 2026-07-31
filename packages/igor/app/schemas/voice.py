"""Wire schemas for voice output (routers/voice.py)."""

from pydantic import BaseModel, Field

from app.services.tts import MAX_CHARS


class SpeakRequest(BaseModel):
    # One utterance. The client sends a sentence at a time so playback can start
    # before the rest of the reply has been synthesised; the cap matches what
    # the TTS service will accept in a single request.
    text: str = Field(min_length=1, max_length=MAX_CHARS)
    # Whose voice to use. Resolved against the agent's profile (Rule 10);
    # omitted for a plain read-aloud that belongs to no particular agent.
    agent_id: str | None = Field(default=None, max_length=48)
    # Explicit override, mainly for auditioning voices from the settings UI.
    voice: str | None = Field(default=None, max_length=64)
