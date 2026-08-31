# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Voice endpoints — both directions.

Voice mode synthesises the reply the orchestrator already produced and
recognises what the owner said into the mic, so this is a transport surface,
not a place decisions get made (Rule 1): resolve the agent's voice from its
profile, hand text or audio to the service, return the other one.

Outbound there are two paths, and which one a turn takes is decided HERE rather
than by the client, because it depends on the resolved voice's engine:

  /voice/stream (WebSocket) is the good one. The reply is fed in as it is
  written and ElevenLabs keeps one prosodic context across the whole turn, so
  intonation carries across a sentence boundary the way a person's does. See
  services/tts_stream.py for why that is worth a second transport.

  /voice/speak (HTTP, per sentence) is the fallback, and the only option for an
  Azure or OpenAI voice: the client splits at sentence boundaries and playback
  of sentence N overlaps synthesis of N+1. That overlap is what keeps replies
  starting quickly, but every sentence is still a standalone utterance with its
  own terminal contour, which is exactly the seam the streaming path removes.

Inbound, the client segments on SILENCE and posts one utterance at a time to
/voice/listen. Segmentation lives on the client because the decision "they have
stopped talking" has to be made where the waveform already is: shipping audio
continuously to the server to find that out would spend bandwidth and Azure
audio-hours on the pauses between sentences, which is most of a conversation.
"""

import asyncio
import hmac
import logging

from fastapi import (
    APIRouter, File, Form, HTTPException, Request, UploadFile, WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import Response

from app.config import settings
from app.schemas.voice import ListenResponse, SpeakRequest
from app.services import stt, tts, tts_stream

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
        # Whether /voice/stream is worth attempting at all. The client still has
        # to open the socket to learn whether THIS agent's voice is streamable
        # (that depends on the resolved engine), but a deployment with no
        # ElevenLabs key can skip the attempt entirely.
        "streaming": tts_stream.streaming_available(),
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


# ── Streaming synthesis ─────────────────────────────────────────────────────
#
# Frames, client → server (JSON):
#   {"type":"auth","key":…,"agent_id":…,"voice":…,"locale":…}   first, once
#   {"type":"text","text":…}    the next piece of the reply, whole words only
#   {"type":"flush"}            generate what is buffered (a pause mid-turn)
#   {"type":"end"}              the reply is finished
#
# Frames, server → client:
#   {"type":"ready","sample_rate":…}   streaming is on; audio follows
#   {"type":"unsupported","reason":…}  this voice cannot stream — use /speak
#   <binary>                           raw PCM, mono s16le, at `sample_rate`
#   {"type":"done"}                    every sample for this turn has been sent
#   {"type":"error","detail":…}        died mid-turn; the client falls back
#
# Audio goes back as BINARY rather than base64 in JSON: it is already bytes by
# the time it reaches here, and re-encoding it would add a third to the size of
# every frame on the latency-sensitive path.

# The owner's client should authenticate immediately. A socket that connects and
# then says nothing is either a scan or a hang, and must not hold a worker.
_AUTH_TIMEOUT_S = 10.0


@router.websocket("/stream")
async def stream(websocket: WebSocket):
    """Synthesize a reply continuously as it is written.

    The engine is decided here, not by the client: only ElevenLabs has a
    streaming-input socket, so a voice resolving to Azure or OpenAI is told
    `unsupported` and expected to fall back to per-sentence /voice/speak. That
    keeps voice resolution in one place (`tts.resolve_voice`) — a client that
    guessed the engine from its own settings would guess wrong for every agent
    whose voice comes from its profile rather than from an explicit pin.
    """
    # AuthMiddleware only covers http-scope requests, so the key is checked
    # here. Unlike the agents socket it arrives in the first FRAME rather than a
    # header: this peer is the desktop renderer, and no browser WebSocket can
    # set a handshake header. A query parameter would put the key in every
    # access log along the way, which a frame does not.
    await websocket.accept()

    try:
        hello = await asyncio.wait_for(websocket.receive_json(), timeout=_AUTH_TIMEOUT_S)
    except (TimeoutError, WebSocketDisconnect, ValueError):
        await websocket.close(code=1008)
        return

    key = str(hello.get("key") or "")
    if not (key and hmac.compare_digest(key, settings.speda_api_key)):
        logger.warning("voice_stream_auth_rejected")
        await websocket.close(code=1008)  # policy violation
        return

    agent_id = hello.get("agent_id") or None
    locale = hello.get("locale") or settings.tts_locale or None
    voice_ref = tts.resolve_voice(websocket.app.state.profiles, agent_id, hello.get("voice"))
    provider, model, voice_id = tts.parse_voice_ref(voice_ref)

    if provider != "elevenlabs" or not tts_stream.streaming_available():
        # Not an error: an Azure voice working exactly as configured still
        # cannot stream, and the client has a working path for it.
        await websocket.send_json({
            "type": "unsupported",
            "reason": f"{provider} voices synthesize whole utterances only.",
        })
        await websocket.close()
        return

    try:
        async with tts_stream.open_stream(
            voice_id, model, tts.resolve_voice_settings(agent_id), locale,
        ) as speech:
            await websocket.send_json({
                "type": "ready", "sample_rate": tts_stream.PCM_SAMPLE_RATE,
            })
            await _pump(websocket, speech, locale)
    except tts_stream.SpeechStreamError as exc:
        # The turn itself is fine — only the voice failed, same reasoning as
        # /speak's 503. Saying so lets the client fall back rather than sit
        # silent in front of a socket that will never speak.
        await websocket.send_json({"type": "error", "detail": str(exc)})
    except WebSocketDisconnect:
        return

    try:
        await websocket.close()
    except RuntimeError:
        pass  # already closed by the disconnect that got us here


async def _pump(websocket: WebSocket, speech, locale: str | None) -> None:
    """Run text-in and audio-out concurrently for one turn.

    They have to be concurrent, not interleaved: audio for the beginning of the
    reply arrives while its end is still being typed, and a loop that waited for
    one before serving the other would reintroduce exactly the stop-start
    delivery this path exists to remove.
    """

    async def feed() -> None:
        while True:
            frame = await websocket.receive_json()
            kind = frame.get("type")
            if kind == "text":
                # The same markdown strip the HTTP path applies, as a net. The
                # client already drops what belongs on the canvas and sends
                # line-complete prose, so this is per-line and cannot straddle a
                # construct — but a stray heading hash read aloud is the one
                # failure nobody would think to look for here.
                spoken = tts.strip_for_speech(str(frame.get("text") or ""), locale)
                if spoken:
                    await speech.send_text(spoken)
            elif kind == "flush":
                await speech.flush()
            elif kind == "end":
                await speech.end_input()
                return

    async def drain() -> None:
        async for pcm in speech.audio():
            await websocket.send_bytes(pcm)
        await websocket.send_json({"type": "done"})

    feeder = asyncio.create_task(feed())
    drainer = asyncio.create_task(drain())
    try:
        # `drain` finishing is what ends the turn — it returns on ElevenLabs'
        # isFinal, which can only follow the `end` that `feed` sent. Waiting on
        # the pair instead would hang whenever the client closes without one.
        done, _ = await asyncio.wait(
            {feeder, drainer}, return_when=asyncio.FIRST_COMPLETED,
        )
        if drainer in done:
            drainer.result()  # re-raise a drain failure rather than swallow it
            return
        # The feeder finished or failed first: either the client went away
        # mid-turn, or `end` was sent and the tail is still generating.
        feeder.result()
        await drainer
    finally:
        for task in (feeder, drainer):
            if not task.done():
                task.cancel()
        await asyncio.gather(feeder, drainer, return_exceptions=True)


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
