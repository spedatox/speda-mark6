# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Live streaming synthesis — ElevenLabs' stream-input WebSocket.

Why this exists alongside `services/tts.py`:

`synthesize` treats one call as one COMPLETE utterance. That is correct for an
automation push or the text_to_speech skill, where the whole text is known
before anything is spoken. Voice mode is not that shape. There the reply arrives
token by token, and the client used to cut it at sentence boundaries and fire
one synthesis per sentence — so every sentence reached the engine as a
standalone utterance, got a full falling terminal contour and its own pacing,
and the seam between two clips landed as a gap. A paragraph came out as a list
of unrelated sentences, which is precisely what "unnatural" sounds like.

The stream-input socket is built for the opposite shape: text goes in
continuously, the model keeps ONE prosodic context across the whole turn, and
audio comes back as it is generated. Intonation carries across a sentence
boundary the way a person's does, because the model can see that the sentence is
not the end of the thought.

Two consequences shape everything here:

1. Output is raw PCM, not MP3. Streamed MP3 frames cannot be handed to the
   browser's `decodeAudioData` one at a time — each chunk is a fragment of a
   stream rather than a file — and stitching decoded fragments is what puts the
   seams back. Raw PCM samples splice EXACTLY, which is what lets the client
   schedule consecutive buffers back-to-back on the audio clock with no gap at
   all. `PCM_SAMPLE_RATE` is the contract with that client and the two must move
   together.

2. This module resolves nothing. It is handed a voice id, a model and settings
   and it speaks (Rule 10 — the CALLER resolves identity). It does not know what
   an agent is.

Failure is always loud here, never silent: unlike `synthesize`, whose caller can
fall back to a quieter degradation, a stream that cannot open must tell the
router so the client can drop back to the per-sentence HTTP path rather than sit
in front of a socket that will never speak.
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from app.config import settings

logger = logging.getLogger(__name__)

_STREAM_URL = "wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input"

# 24 kHz signed 16-bit little-endian mono. The client rebuilds AudioBuffers from
# this and MUST agree on all four facts, so they are stated once, here.
PCM_SAMPLE_RATE = 24000
_OUTPUT_FORMAT = f"pcm_{PCM_SAMPLE_RATE}"

# How many characters ElevenLabs buffers before generating each successive
# chunk. Their documented default, kept deliberately: a smaller first value
# would shave latency off the first word at the cost of the model seeing less
# context before it commits to a contour — and context is the entire reason this
# path exists. The overlap that hides the remaining latency is the client's job.
_CHUNK_SCHEDULE = [120, 160, 250, 290]

# The socket closes itself after this long with no text. The default is 20s,
# which a turn routinely exceeds while a tool call runs mid-reply — that would
# drop the stream in the middle of an answer. 180 is the documented maximum.
_INACTIVITY_TIMEOUT = 180

# A stream carries one turn. Past this the peer is not going to say anything
# useful and the socket is a leak.
_OPEN_TIMEOUT = 10
_CLOSE_TIMEOUT = 5


class SpeechStreamError(RuntimeError):
    """The stream could not be opened or died mid-turn. Message is safe to show."""


def streaming_available() -> bool:
    """Whether the live streaming path can be used at all.

    Only ElevenLabs offers a streaming-input socket; Azure and OpenAI synthesize
    complete utterances only, so a voice on either of those engines keeps the
    per-sentence HTTP path. Callers use this to decide, not to apologise.
    """
    return bool(settings.elevenlabs_api_key)


class SpeechStream:
    """One turn's worth of synthesis over an open stream-input socket.

    Text in via `send_text`, audio out via `audio()`. The two are independent —
    the caller is expected to pump them concurrently, because the whole point is
    that audio for the beginning of the turn arrives while its end is still
    being written.
    """

    def __init__(self, ws) -> None:
        self._ws = ws

    async def send_text(self, text: str) -> None:
        """Feed the next piece of the reply.

        The caller must send WHOLE WORDS. ElevenLabs concatenates what it is
        given and wants each message to end with a single space, so a chunk cut
        mid-word ("Hel" + "lo") would be spoken as two. Every caller here feeds
        line-complete prose, which satisfies that by construction; the trailing
        newline is normalised to the space the protocol asks for.
        """
        payload = text.rstrip()
        if not payload:
            return
        await self._ws.send(json.dumps({"text": payload + " "}))

    async def flush(self) -> None:
        """Generate whatever is buffered without ending the stream.

        Only for a real pause in the reply — a tool call the owner is waiting
        through. Flushing routinely would defeat the module: each flush closes
        off a prosodic unit, which is the per-sentence behaviour this path
        exists to stop doing.
        """
        await self._ws.send(json.dumps({"text": " ", "flush": True}))

    async def end_input(self) -> None:
        """The turn is over. Generation of the tail begins; audio still follows."""
        await self._ws.send(json.dumps({"text": ""}))

    async def audio(self) -> AsyncIterator[bytes]:
        """Yield PCM frames until the turn's audio is complete.

        Ends on `isFinal`. A frame with no audio is a bare alignment update and
        is skipped rather than yielded as an empty buffer the client would have
        to guard against.
        """
        async for raw in self._ws:
            try:
                frame = json.loads(raw)
            except (TypeError, ValueError):
                logger.warning("tts_stream_bad_frame")
                continue
            chunk = frame.get("audio")
            if chunk:
                yield base64.b64decode(chunk)
            if frame.get("isFinal"):
                return


@asynccontextmanager
async def open_stream(
    voice_id: str, model: str = "", voice_settings: dict | None = None,
    language: str | None = None,
) -> AsyncIterator[SpeechStream]:
    """Open a stream-input socket for one turn and hand back a `SpeechStream`.

    `voice_settings` is omitted entirely when empty, for the same reason
    `_elevenlabs_synthesize` omits it: ElevenLabs falls back to the voice's own
    dashboard settings only when the key is ABSENT, and sending `{}` would
    replace a tuned voice with library defaults nobody asked for.

    `language` pins the spoken language where the model supports it. The
    multilingual models infer it from the text, so this only settles the
    ambiguous cases — the same job `instructions` does on the OpenAI path.
    """
    if not settings.elevenlabs_api_key:
        raise SpeechStreamError("Streaming voice needs ELEVENLABS_API_KEY.")

    params = [
        f"model_id={model or 'eleven_multilingual_v2'}",
        f"output_format={_OUTPUT_FORMAT}",
        f"inactivity_timeout={_INACTIVITY_TIMEOUT}",
    ]
    # Only the two-letter subtag: the API wants "tr", not the "tr-TR" the rest
    # of this codebase passes around.
    if language:
        params.append(f"language_code={language.split('-')[0].lower()}")
    url = _STREAM_URL.format(voice_id=voice_id) + "?" + "&".join(params)

    opening: dict = {
        "text": " ",
        "generation_config": {"chunk_length_schedule": _CHUNK_SCHEDULE},
    }
    if voice_settings:
        opening["voice_settings"] = voice_settings

    try:
        async with connect(
            url,
            additional_headers={"xi-api-key": settings.elevenlabs_api_key},
            open_timeout=_OPEN_TIMEOUT,
            close_timeout=_CLOSE_TIMEOUT,
            # Frames are small JSON; the default cap is generous but a runaway
            # peer should not be able to grow this process's memory.
            max_size=4 * 1024 * 1024,
        ) as ws:
            await ws.send(json.dumps(opening))
            logger.info(
                "tts_stream_open",
                extra={"model": model, "voice": voice_id, "language": language or ""},
            )
            yield SpeechStream(ws)
    except WebSocketException as exc:
        logger.warning(
            "tts_stream_failed",
            extra={"error": str(exc), "model": model, "voice": voice_id},
        )
        raise SpeechStreamError("ElevenLabs streaming voice is unavailable.") from exc
    except OSError as exc:
        logger.warning("tts_stream_transport_error", extra={"error": str(exc)})
        raise SpeechStreamError("Could not reach ElevenLabs streaming voice.") from exc
