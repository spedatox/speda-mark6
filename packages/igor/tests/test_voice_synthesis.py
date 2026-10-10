# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Provider contracts for v4 HTTP output and incremental dialogue streaming."""
import asyncio
import base64
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.services import tts, tts_stream


def _http(monkeypatch, handler):
    client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(tts.httpx, "AsyncClient", lambda **kwargs: client(transport=transport, **kwargs))
    monkeypatch.setattr(tts.settings, "elevenlabs_api_key", "test-eleven-key")


@pytest.mark.parametrize("model", ["eleven_v4", "eleven_multilingual_v2", ""])
def test_http_speech_upgrades_saved_models_and_only_sends_v4_controls(monkeypatch, model):
    def respond(request):
        assert str(request.url) == "https://api.elevenlabs.io/v1/text-to-speech/custom-voice"
        assert request.headers["xi-api-key"] == "test-eleven-key"
        assert json.loads(request.content) == {
            "text": "Merhaba.", "model_id": "eleven_v4", "language_code": "tr",
            "voice_settings": {"stability": 0.4, "similarity_boost": 0.8},
        }
        return httpx.Response(200, content=b"mp3-audio")

    _http(monkeypatch, respond)
    audio = asyncio.run(tts.synthesize_prepared(
        "Merhaba.", f"elevenlabs:{model}:custom-voice", "tr-TR",
        {"stability": 0.4, "similarity_boost": 0.8, "style": 0.5, "speed": 1.2, "use_speaker_boost": True},
    ))
    assert audio == b"mp3-audio"


def test_unused_v4_controls_do_not_override_dashboard_defaults(monkeypatch):
    def respond(request):
        assert "voice_settings" not in json.loads(request.content)
        return httpx.Response(200, content=b"mp3")

    _http(monkeypatch, respond)
    asyncio.run(tts.synthesize_prepared("Hello.", "elevenlabs:eleven_v4:custom", "en-US", {"speed": 1.2}))


def test_voice_catalog_offers_v4_with_the_owners_voice_ids(monkeypatch):
    _http(monkeypatch, lambda request: httpx.Response(200, json={
        "voices": [{"voice_id": "custom", "name": "My voice", "labels": {"gender": "female"}}],
    }))
    voices = asyncio.run(tts._elevenlabs_voices())
    assert voices[0]["id"] == "elevenlabs:eleven_v4:custom"
    assert voices[0]["model"] == "eleven_v4"


def test_saved_pin_upgrades_without_losing_voice_or_overriding_another_provider(monkeypatch):
    from app.core import runtime_state

    monkeypatch.setattr(runtime_state, "get_voice_overrides", lambda: {
        "test-agent": {"voice_id": "elevenlabs:eleven_multilingual_v2:custom"},
    })
    profile = SimpleNamespace(voice_id="elevenlabs:eleven_v4:profile")
    assert tts.resolve_voice(None, "test-agent", profile=profile) == "elevenlabs:eleven_v4:custom"
    assert tts.resolve_voice(None, "test-agent", "openai:tts-1:nova", profile) == "openai:tts-1:nova"


class _Socket:
    def __init__(self, frames):
        self.frames = frames
        self.sent = []

    async def send(self, raw):
        self.sent.append(json.loads(raw))

    def __aiter__(self):
        async def iterate():
            for frame in self.frames:
                yield json.dumps(frame)
        return iterate()


@pytest.mark.parametrize("model", ["", "eleven_multilingual_v2", "eleven_v4", "eleven_v4_turbo"])
def test_dialogue_protocol_delivers_tail_audio_and_preserves_one_turn(monkeypatch, model):
    socket = _Socket([
        {"audio": base64.b64encode(b"\x01\x02").decode()},
        {"is_final_audio_for_turn": True},
        {"audio": base64.b64encode(b"\x03\x04").decode(), "is_final": True},
        {"audio": base64.b64encode(b"unreachable").decode()},
    ])

    @asynccontextmanager
    async def connect(url, **kwargs):
        parts = urlsplit(url)
        assert parts.path == "/v1/text-to-dialogue/stream-input"
        assert parse_qs(parts.query) == {
            "model_id": ["eleven_v4_turbo" if model == "eleven_v4_turbo" else "eleven_v4"],
            "output_format": ["pcm_24000"], "language_code": ["tr"],
        }
        assert kwargs["additional_headers"] == {"xi-api-key": "test-eleven-key"}
        yield socket

    monkeypatch.setattr(tts_stream, "connect", connect)
    monkeypatch.setattr(tts_stream.settings, "elevenlabs_api_key", "test-eleven-key")

    async def run():
        async with tts_stream.open_stream("custom", model, {"stability": 0.3, "speed": 1.2}, "tr-TR") as speech:
            await speech.send_text("First line.")
            await speech.flush()
            await speech.send_text("Last line.")
            await speech.end_input()
            return [pcm async for pcm in speech.audio()]

    assert asyncio.run(run()) == [b"\x01\x02", b"\x03\x04"]
    assert socket.sent == [
        {"voices": ["custom"], "voice_settings": {"stability": 0.3}},
        {"inputs": [{"text": "First line. ", "voice_id": "custom", "new_turn": False}]},
        {"flush": True},
        {"inputs": [{"text": "Last line. ", "voice_id": "custom", "new_turn": False}]},
        {"close_socket": True},
    ]


def test_stream_reports_provider_errors_instead_of_silently_finishing():
    async def run():
        stream = tts_stream.SpeechStream(_Socket([{"error": "invalid_voice", "message": "not available"}]), "custom")
        return [pcm async for pcm in stream.audio()]

    with pytest.raises(tts_stream.SpeechStreamError, match="rejected"):
        asyncio.run(run())


def test_dialogue_keepalive_stops_when_input_ends(monkeypatch):
    original_wait = asyncio.wait_for

    async def short_wait(awaitable, timeout):
        return await original_wait(awaitable, timeout=0.001)

    monkeypatch.setattr(tts_stream.asyncio, "wait_for", short_wait)
    socket = _Socket([])

    async def run():
        stream = tts_stream.SpeechStream(socket, "custom")
        heartbeat = asyncio.create_task(stream.keep_alive())
        await asyncio.sleep(0.01)
        await stream.end_input()
        await heartbeat
        count = len(socket.sent)
        await asyncio.sleep(0.005)
        assert len(socket.sent) == count

    asyncio.run(run())
    assert {"keep_alive": True} in socket.sent
    assert socket.sent[-1] == {"close_socket": True}


def test_flash_voice_keeps_the_existing_tts_socket_protocol(monkeypatch):
    socket = _Socket([{"isFinal": True}])

    @asynccontextmanager
    async def connect(url, **kwargs):
        assert urlsplit(url).path == "/v1/text-to-speech/custom/stream-input"
        assert parse_qs(urlsplit(url).query)["model_id"] == ["eleven_flash_v2_5"]
        yield socket

    monkeypatch.setattr(tts_stream, "connect", connect)
    monkeypatch.setattr(tts_stream.settings, "elevenlabs_api_key", "test-eleven-key")

    async def run():
        async with tts_stream.open_stream("custom", "eleven_flash_v2_5") as stream:
            await stream.send_text("Hello.")
            await stream.end_input()
            assert [pcm async for pcm in stream.audio()] == []

    asyncio.run(run())
    assert socket.sent[0]["text"] == " "
    assert socket.sent[-2:] == [{"text": "Hello. "}, {"text": ""}]
