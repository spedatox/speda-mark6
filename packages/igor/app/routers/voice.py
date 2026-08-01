"""
Voice endpoints — both directions.

Voice mode synthesises the reply the orchestrator already produced and
recognises what the owner said into the mic, so this is a transport surface,
not a place decisions get made (Rule 1): resolve the agent's voice from its
profile, hand text or audio to the service, return the other one.

Outbound, the client splits a streaming reply at sentence boundaries and calls
/voice/speak per sentence, so playback of sentence N overlaps synthesis of N+1 —
that overlap is the only reason spoken replies start quickly instead of after
the full turn.

Inbound, the client segments on SILENCE and posts one utterance at a time to
/voice/listen. Segmentation lives on the client because the decision "they have
stopped talking" has to be made where the waveform already is: shipping audio
continuously to the server to find that out would spend bandwidth and Azure
audio-hours on the pauses between sentences, which is most of a conversation.
"""

import logging

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response

from app.config import settings
from app.schemas.voice import ListenResponse, SpeakRequest
from app.services import stt, tts

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
    """Whether voice is usable, so the client can hide the control instead of
    offering a button that always fails.

    `speak` and `listen` are reported separately even though one key enables
    both: they fail independently at the region, and a mic button that silently
    does nothing is a worse failure than one that is simply absent.
    """
    return {
        "configured": tts.configured(),
        "speak": tts.configured(),
        "listen": stt.configured(),
        "default_voice": settings.tts_default_voice,
        # Which engines can actually speak, so the picker offers only those.
        "providers": tts.providers(),
    }


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


@router.post("/listen", response_model=ListenResponse)
async def listen(
    audio: UploadFile = File(..., description="One utterance, 16 kHz mono PCM WAV."),
    locale: str | None = Form(default=None),
) -> ListenResponse:
    """Transcribe one recorded utterance.

    Multipart rather than JSON: the client already holds the recording as
    binary, and base64 in a JSON body would inflate every utterance by a third
    for no gain.

    A silent recording comes back as `{"text": ""}` with HTTP 200, NOT as an
    error. The client segments on silence, so it will occasionally send a cough
    or a chair creak, and those are non-events — surfacing them as failures
    would flood a conversation with red cards for ordinary background noise.
    """
    raw = await audio.read()
    try:
        text = await stt.transcribe(raw, locale)
    except stt.STTError as exc:
        # 503 for the same reason /speak uses it: the backend is fine, only the
        # voice path is unavailable, and the client falls back to the keyboard.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return ListenResponse(
        text=text,
        locale=locale or settings.stt_locale or settings.tts_locale or "en-US",
    )
