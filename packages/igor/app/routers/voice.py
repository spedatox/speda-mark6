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
    return tts.resolve_voice(request.app.state.profiles, agent_id, explicit)


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


@router.get("/agents")
async def voice_agents(request: Request):
    """Every dispatch-target agent's voice config, for Settings → Voices: the
    profile's own default, the owner's pin (if any), the effective voice
    actually in use, and the tuning override. Session-scope aliases (warroom)
    are excluded the same way the model matrix excludes them — they mirror
    their parent's brain and are not something that should have its own
    voice."""
    from app.core.runtime_state import get_voice_overrides

    overrides = get_voice_overrides()
    out = []
    for p in request.app.state.profiles.roster():
        if not p.dispatch_target:
            continue
        override = overrides.get(p.agent_id, {})
        out.append({
            "agent_id": p.agent_id,
            "name": p.name,
            "domain": p.domain,
            "default_voice": p.voice_id or settings.tts_default_voice,
            "voice_id": override.get("voice_id") or None,
            "effective_voice": tts.resolve_voice(None, p.agent_id, profile=p),
            "voice_settings": {k: override[k] for k in tts.VOICE_SETTINGS_KEYS if k in override} or None,
        })
    return {"agents": out}


@router.put("/agents/{agent_id}")
async def set_voice_agent(agent_id: str, request: Request, body: dict):
    """Pin an agent's voice and/or tune its ElevenLabs settings from Settings
    → Voices. Body: any of voice_id, stability, similarity_boost, style,
    speed, use_speaker_boost — send `null`/omit a key to leave it as is, or
    send the WHOLE body empty to clear every override for this agent back to
    the profile default. Fields outside VOICE_SETTINGS_KEYS are ignored, not
    rejected — a stray key must not fail the whole save.
    """
    from app.core.runtime_state import get_voice_overrides, set_voice_override

    if request.app.state.profiles.get(agent_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown agent '{agent_id}'")

    current = dict(get_voice_overrides().get(agent_id, {}))
    if not body:
        set_voice_override(agent_id, None)
    else:
        if "voice_id" in body:
            if body["voice_id"]:
                current["voice_id"] = str(body["voice_id"])
            else:
                current.pop("voice_id", None)
        for key in tts.VOICE_SETTINGS_KEYS:
            if key not in body:
                continue
            if body[key] is None:
                current.pop(key, None)
            else:
                current[key] = body[key]
        set_voice_override(agent_id, current or None)
    return await voice_agents(request)


@router.post("/speak")
async def speak(request: Request, body: SpeakRequest):
    """Synthesize one utterance and return the audio bytes."""
    voice = _resolve_voice(request, body.agent_id, body.voice)
    # Same tuning the owner set in Settings → Voices applies here too — voice
    # mode and an automation's "reply as voice" speak with the same identity,
    # not two configurations of the same agent.
    voice_settings = tts.resolve_voice_settings(body.agent_id)
    try:
        audio = await tts.synthesize(body.text, voice, body.locale, voice_settings)
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
