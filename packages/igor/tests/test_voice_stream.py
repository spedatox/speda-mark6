# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""WS /voice/stream — the live streaming synthesis socket.

Tested against the route function with a fake WebSocket, the same way
test_chat_steer_route does: the endpoint only touches app.state.profiles and the
tts services, so a SimpleNamespace stands in for the whole app.

What matters here is the NEGOTIATION and the pump, not synthesis: the client
commits to a delivery path based on what this endpoint answers, and a wrong
answer either strands it in front of a silent socket or drops it onto the slow
path when the good one was available.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import WebSocketDisconnect

from app.routers import voice as voice_router


class _FakeWS:
    """Records what the endpoint said, and replays queued client frames."""

    def __init__(self, frames: list[dict]):
        self._frames = list(frames)
        self.accepted = False
        self.sent: list[dict] = []
        self.audio: list[bytes] = []
        self.closed_with: int | None = None
        self.app = SimpleNamespace(state=SimpleNamespace(profiles=None))

    async def accept(self):
        self.accepted = True

    async def receive_json(self):
        if not self._frames:
            # The client went away — exactly what Starlette raises.
            raise WebSocketDisconnect(1000)
        return self._frames.pop(0)

    async def send_json(self, payload):
        self.sent.append(payload)

    async def send_bytes(self, data):
        self.audio.append(data)

    async def close(self, code: int = 1000):
        self.closed_with = code

    @property
    def kinds(self) -> list[str]:
        return [m.get("type") for m in self.sent]


class _FakeStream:
    """Stands in for an open ElevenLabs socket."""

    def __init__(self):
        self.text: list[str] = []
        self.flushes = 0
        self.ended = asyncio.Event()

    async def send_text(self, text: str):
        self.text.append(text)

    async def flush(self):
        self.flushes += 1

    async def end_input(self):
        self.ended.set()

    async def audio(self):
        # One frame straight away — audio for the start of a reply must be able
        # to flow while its end is still being written — then the tail once the
        # input is closed.
        yield b"\x01\x02"
        await self.ended.wait()
        yield b"\x03\x04"


def _elevenlabs(monkeypatch, stream: _FakeStream | None = None):
    """Point the endpoint at a fake engine and a streamable default voice."""
    monkeypatch.setattr(voice_router.tts_stream, "streaming_available", lambda: True)
    monkeypatch.setattr(voice_router.settings, "speda_api_key", "test-key")

    @asynccontextmanager
    async def _open(*_args, **_kwargs):
        yield stream if stream is not None else _FakeStream()

    monkeypatch.setattr(voice_router.tts_stream, "open_stream", _open)


VOICE = "elevenlabs:eleven_multilingual_v2:abc123"


def test_a_wrong_key_is_closed_before_anything_is_synthesized(monkeypatch):
    monkeypatch.setattr(voice_router.settings, "speda_api_key", "test-key")
    ws = _FakeWS([{"type": "auth", "key": "not-the-key", "voice": VOICE}])

    asyncio.run(voice_router.stream(ws))

    assert ws.closed_with == 1008
    assert ws.sent == []


def test_a_missing_auth_frame_is_closed(monkeypatch):
    monkeypatch.setattr(voice_router.settings, "speda_api_key", "test-key")
    ws = _FakeWS([])  # client connects, then says nothing

    asyncio.run(voice_router.stream(ws))

    assert ws.closed_with == 1008


def test_a_non_elevenlabs_voice_is_told_to_use_the_http_path(monkeypatch):
    # An Azure voice working exactly as configured still cannot stream. The
    # client needs to hear that as `unsupported`, not as an error, or it has no
    # reason to fall back.
    monkeypatch.setattr(voice_router.tts_stream, "streaming_available", lambda: True)
    monkeypatch.setattr(voice_router.settings, "speda_api_key", "test-key")
    ws = _FakeWS([{"type": "auth", "key": "test-key", "voice": "azure:tr-TR-EmelNeural"}])

    asyncio.run(voice_router.stream(ws))

    assert ws.kinds == ["unsupported"]


def test_a_deployment_without_an_elevenlabs_key_is_unsupported(monkeypatch):
    monkeypatch.setattr(voice_router.tts_stream, "streaming_available", lambda: False)
    monkeypatch.setattr(voice_router.settings, "speda_api_key", "test-key")
    ws = _FakeWS([{"type": "auth", "key": "test-key", "voice": VOICE}])

    asyncio.run(voice_router.stream(ws))

    assert ws.kinds == ["unsupported"]


def test_text_reaches_the_engine_and_audio_comes_back(monkeypatch):
    stream = _FakeStream()
    _elevenlabs(monkeypatch, stream)
    ws = _FakeWS([
        {"type": "auth", "key": "test-key", "voice": VOICE},
        {"type": "text", "text": "Merhaba."},
        {"type": "text", "text": "Nasilsin?"},
        {"type": "end"},
    ])

    asyncio.run(voice_router.stream(ws))

    assert stream.text == ["Merhaba.", "Nasilsin?"]
    assert stream.ended.is_set()
    # Both frames, in order, as binary rather than base64 in JSON.
    assert ws.audio == [b"\x01\x02", b"\x03\x04"]
    assert ws.kinds == ["ready", "done"]


def test_a_pause_flushes_without_ending_the_turn(monkeypatch):
    stream = _FakeStream()
    _elevenlabs(monkeypatch, stream)
    ws = _FakeWS([
        {"type": "auth", "key": "test-key", "voice": VOICE},
        {"type": "text", "text": "Checking that now."},
        {"type": "flush"},
        {"type": "text", "text": "It is done."},
        {"type": "end"},
    ])

    asyncio.run(voice_router.stream(ws))

    assert stream.flushes == 1
    assert stream.text == ["Checking that now.", "It is done."]
    assert ws.kinds == ["ready", "done"]


def test_markdown_is_stripped_before_it_is_spoken(monkeypatch):
    # The client already drops what belongs on the canvas, but a stray heading
    # hash read aloud is the failure nobody would think to look for.
    stream = _FakeStream()
    _elevenlabs(monkeypatch, stream)
    ws = _FakeWS([
        {"type": "auth", "key": "test-key", "voice": VOICE},
        {"type": "text", "text": "## Sonuc"},
        {"type": "text", "text": "**Onemli** bir nokta."},
        {"type": "end"},
    ])

    asyncio.run(voice_router.stream(ws))

    assert stream.text == ["Sonuc", "Onemli bir nokta."]


def test_a_client_that_vanishes_mid_turn_does_not_raise(monkeypatch):
    # Barge-in: the owner interrupts and the socket goes away without an `end`.
    stream = _FakeStream()
    _elevenlabs(monkeypatch, stream)
    ws = _FakeWS([
        {"type": "auth", "key": "test-key", "voice": VOICE},
        {"type": "text", "text": "Half a sentence"},
    ])

    asyncio.run(voice_router.stream(ws))

    assert stream.text == ["Half a sentence"]
    assert "done" not in ws.kinds


def test_an_engine_that_will_not_open_is_reported_so_the_client_can_fall_back(monkeypatch):
    monkeypatch.setattr(voice_router.tts_stream, "streaming_available", lambda: True)
    monkeypatch.setattr(voice_router.settings, "speda_api_key", "test-key")

    @asynccontextmanager
    async def _refuse(*_args, **_kwargs):
        raise voice_router.tts_stream.SpeechStreamError("ElevenLabs is unavailable.")
        yield  # pragma: no cover — makes this an async generator

    monkeypatch.setattr(voice_router.tts_stream, "open_stream", _refuse)
    ws = _FakeWS([{"type": "auth", "key": "test-key", "voice": VOICE}])

    asyncio.run(voice_router.stream(ws))

    assert ws.kinds == ["error"]
    assert "unavailable" in ws.sent[0]["detail"]


@pytest.mark.parametrize("ref,streamable", [
    ("elevenlabs:eleven_multilingual_v2:abc", True),
    ("azure:tr-TR-EmelNeural", False),
    ("openai:gpt-4o-mini-tts:nova", False),
    # A bare name is the pre-multi-provider Azure form and must stay Azure.
    ("tr-TR-EmelNeural", False),
])
def test_only_elevenlabs_refs_negotiate_a_stream(monkeypatch, ref, streamable):
    _elevenlabs(monkeypatch)
    ws = _FakeWS([
        {"type": "auth", "key": "test-key", "voice": ref},
        {"type": "end"},
    ])

    asyncio.run(voice_router.stream(ws))

    assert ("ready" in ws.kinds) is streamable
