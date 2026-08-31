# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Speech-to-text recognition — Azure Speech.

The mirror of services/tts.py, and deliberately shaped the same way: voice mode
listens on the owner's gesture, so recognition is a TRANSPORT concern driven by
the router, never a tool the model elects to call. (The `speech_to_text` skill
still exists for "transcribe this file I uploaded" and is backed by this module.)

Azure Speech is ONE resource: the `AZURE_SPEECH_KEY` already configured for
spoken replies covers recognition too, on a different hostname. Voice input
therefore costs no second credential and no second sign-up — if the agent can
speak, it can already listen.

Two things here are load-bearing:

1. Silence is NOT an error. Azure answers `NoMatch` or `InitialSilenceTimeout`
   when the owner opened the mic and said nothing, which in a conversation UI is
   an ordinary event — a pause, a false trigger, a throat clear. It returns an
   empty transcript and the caller drops it. Raising here would put a red error
   card on screen every time somebody hesitated.

2. The endpoint takes SHORT audio only — 60 seconds, hard. The client segments
   on silence so a turn should never come close, but a caller that streams in
   something long deserves a readable message rather than an opaque upstream
   400.
"""

from __future__ import annotations

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Azure's short-audio recognition endpoint cuts off at 60s. The client's own
# silence detection should end a segment long before this; the cap exists so a
# runaway recording fails with a sentence the owner can act on.
MAX_AUDIO_BYTES = 16_000 * 2 * 60          # 16 kHz, 16-bit mono, 60 seconds

# Recognition waits on the speaker, so it gets a longer read timeout than
# synthesis — but the connect timeout stays short, because an unreachable region
# should fail fast rather than hold the mic open.
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

# What the client records. Declared here rather than taken from the upload's
# Content-Type: Azure rejects a mismatch outright, and a browser's idea of its
# own MIME type is not something to hand an upstream API unverified.
WAV_CONTENT_TYPE = "audio/wav; codecs=audio/pcm; samplerate=16000"

# The short-audio endpoint accepts exactly these containers. Anything else needs
# transcoding, which would mean an ffmpeg dependency on Contabo to serve a case
# the voice client never produces — so unsupported input gets a clear message
# instead, and the owner converts it or we add the dependency deliberately.
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"RIFF", WAV_CONTENT_TYPE),
    (b"OggS", "audio/ogg; codecs=opus"),
)


def sniff_content_type(audio: bytes) -> str | None:
    """Identify the container from its magic bytes, or None if unsupported.

    Sniffed rather than trusted from the upload: a file's extension and its
    declared MIME type are both things a caller can get wrong, and Azure
    answers a mismatch with an opaque 400 that reads like a bad key.
    """
    for magic, content_type in _MAGIC:
        if audio.startswith(magic):
            return content_type
    return None

# Statuses that mean "nobody said anything", as opposed to "recognition broke".
# Treated as an empty result, never as a failure — see the module docstring.
_SILENT_STATUSES = {"NoMatch", "InitialSilenceTimeout", "BabbleTimeout"}


def configured() -> bool:
    """Whether voice input is available at all. Callers degrade, never crash."""
    return bool(settings.azure_speech_key)


def _endpoint() -> str:
    # Note `stt.speech`, not `tts.speech` — same resource, same key, different
    # host. Getting this wrong yields a 404 that reads like a bad credential.
    region = settings.azure_speech_region.strip()
    return f"https://{region}.stt.speech.microsoft.com/speech/recognition/conversation/cognitiveservices/v1"


class STTError(RuntimeError):
    """Recognition failed. Carries a message safe to show the owner."""


async def transcribe(
    audio: bytes,
    locale: str | None = None,
    content_type: str | None = None,
) -> str:
    """Recognize one utterance and return its text.

    `locale` is the language being SPOKEN, e.g. "tr-TR". Unlike synthesis there
    is no multilingual voice to disambiguate — Azure needs to be told which
    language to decode, and guessing wrong does not degrade gracefully, it
    returns confident nonsense. Defaults to settings.stt_locale, then to the
    language the agent itself speaks (settings.tts_locale), so a single TR/EN
    toggle in the client moves both directions of the conversation at once.

    `content_type` defaults to the 16 kHz mono WAV the voice client records.
    Pass a sniffed type (see sniff_content_type) when the audio came from
    somewhere else, such as an uploaded file.

    Returns "" when the owner said nothing — an empty transcript is a normal
    outcome and callers should simply not send a turn. Raises STTError only when
    recognition genuinely failed: no key, oversized audio, or an upstream error.
    """
    if not configured():
        raise STTError("Voice input is not configured — set AZURE_SPEECH_KEY.")
    if not audio:
        raise STTError("No audio was received.")
    if len(audio) > MAX_AUDIO_BYTES:
        raise STTError("That recording is too long — Azure recognizes up to 60 seconds at a time.")

    spoken_locale = locale or settings.stt_locale or settings.tts_locale or "en-US"

    headers = {
        "Ocp-Apim-Subscription-Key": settings.azure_speech_key,
        "Content-Type": content_type or WAV_CONTENT_TYPE,
        "Accept": "application/json",
        # Azure rejects requests without a User-Agent.
        "User-Agent": "speda-mark-vi",
    }
    params = {"language": spoken_locale, "profanity": "raw"}

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(_endpoint(), headers=headers, params=params, content=audio)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        # Same failure taxonomy as synthesis: naming the key/region case is what
        # stops an afternoon of hunting through the wrong layer.
        detail = {
            400: "could not decode the audio (expected 16 kHz mono WAV)",
            401: "rejected the key",
            403: "rejected the key (check the region matches the key)",
            429: "rate-limited the request",
        }.get(status, f"returned HTTP {status}")
        logger.warning(
            "stt_upstream_error",
            extra={"status": status, "locale": spoken_locale, "body": exc.response.text[:300]},
        )
        raise STTError(f"Azure Speech {detail}.") from exc
    except httpx.HTTPError as exc:
        logger.warning("stt_transport_error", extra={"error": str(exc)})
        raise STTError("Could not reach Azure Speech.") from exc
    except ValueError as exc:
        # A 200 that is not JSON means the response shape changed under us.
        raise STTError("Azure Speech returned an unreadable response.") from exc

    status_text = data.get("RecognitionStatus", "")
    if status_text in _SILENT_STATUSES:
        logger.info("stt_silent", extra={"status": status_text, "locale": spoken_locale})
        return ""
    if status_text != "Success":
        logger.warning("stt_failed", extra={"status": status_text, "locale": spoken_locale})
        raise STTError(f"Recognition failed ({status_text or 'unknown'}).")

    text = (data.get("DisplayText") or "").strip()
    logger.info(
        "stt_recognized",
        extra={"locale": spoken_locale, "bytes": len(audio), "chars": len(text)},
    )
    return text
