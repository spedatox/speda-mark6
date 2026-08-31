# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Wire schemas for voice input and output (routers/voice.py)."""

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
    # Explicit override — the owner's chosen voice, or an audition from the
    # settings UI. A full ref: "openai:gpt-4o-mini-tts:nova", "azure:tr-TR-EmelNeural",
    # or a bare Azure voice name (see services/tts.py::parse_voice_ref).
    voice: str | None = Field(default=None, max_length=96)
    # Language of `text`, e.g. "tr-TR". Only needed to speak something other
    # than the owner's usual language (settings.tts_locale) — a multilingual
    # voice reads whatever this says, regardless of its own en-US name.
    locale: str | None = Field(default=None, max_length=16)


class ListenResponse(BaseModel):
    # What was heard. EMPTY IS NORMAL — the owner opened the mic and said
    # nothing, or the segmenter fired on a cough. The client drops an empty
    # transcript instead of sending a turn, so this must not be an error shape.
    text: str = ""
    # Which language it was decoded as, echoed back so the client can show what
    # it actually listened for when a transcript comes back wrong. A TR/EN
    # mismatch is by far the most common cause of nonsense output.
    locale: str = ""
