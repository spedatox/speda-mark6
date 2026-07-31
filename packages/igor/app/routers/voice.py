"""
Voice output endpoints.

Voice mode synthesises the reply the orchestrator already produced, so this is
a transport surface, not a place decisions get made (Rule 1): resolve the
agent's voice from its profile, hand the text to the TTS service, return audio.

The client splits a streaming reply at sentence boundaries and calls /voice/speak
per sentence, so playback of sentence N overlaps synthesis of N+1 — that overlap
is the only reason spoken replies start quickly instead of after the full turn.
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from app.config import settings
from app.schemas.voice import SpeakRequest
from app.services import tts

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/voice", tags=["voice"])


def _resolve_voice(request: Request, agent_id: str | None, explicit: str | None) -> str:
    """Explicit request wins, then the agent's profile, then the engine default."""
    if explicit:
        return explicit
    if agent_id:
        profile = request.app.state.profiles.get(agent_id)
        if profile is not None and profile.voice_id:
            return profile.voice_id
    return settings.tts_default_voice


@router.get("/status")
async def voice_status():
    """Whether voice output is usable, so the client can hide the control
    instead of offering a button that always fails."""
    return {"configured": tts.configured(), "default_voice": settings.tts_default_voice}


@router.get("/voices")
async def voices():
    """Voices available in the configured region — populates the voice picker."""
    return {"voices": await tts.list_voices()}


@router.post("/speak")
async def speak(request: Request, body: SpeakRequest):
    """Synthesize one utterance and return the audio bytes."""
    voice = _resolve_voice(request, body.agent_id, body.voice)
    try:
        audio = await tts.synthesize(body.text, voice, body.locale)
    except tts.TTSError as exc:
        # 503, not 500: the turn itself is fine, only the voice is unavailable,
        # and the client is expected to fall back to a silent reply.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(
        content=audio,
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-store", "X-Voice": voice},
    )
