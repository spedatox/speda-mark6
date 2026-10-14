# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Language pins must survive restarts, partial edits and older-client resets."""

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.core import runtime_state
from app.routers import voice
from app.schemas.voice import SpeakRequest, VoiceAgentPatch
from app.services import tts, voice_preferences


@pytest.fixture
def profiles(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime_state, "_STATE_FILE", tmp_path / "runtime.json")
    monkeypatch.setattr(runtime_state, "_cache", {})
    profile = SimpleNamespace(agent_id="test-agent", name="Test", domain="test", dispatch_target=True,
                              voice_id="openai:tts-1:alloy")
    alias = SimpleNamespace(agent_id="alias", dispatch_target=False)
    registry = SimpleNamespace(get=lambda agent_id: {"test-agent": profile, "alias": alias}.get(agent_id),
                               roster=lambda: [profile, alias])
    return registry


def save(profiles, **changes):
    return voice_preferences.update_agent(profiles, "test-agent", VoiceAgentPatch(**changes))


def test_language_pins_and_tuning_survive_restart_and_preserve_shared_fallback(profiles, monkeypatch):
    save(profiles, voice_id="openai:tts-1:nova", stability=0.6, speed=0.8)
    save(profiles, language="tr", voice_id="elevenlabs:eleven_v4:turkish", stability=0.2)
    save(profiles, language="en", voice_id="elevenlabs:eleven_v4:english", stability=0.9, speed=1.1)
    monkeypatch.setattr(runtime_state, "_cache", None)
    assert tts.resolve_voice(profiles, "test-agent", locale="TR_tr") == "elevenlabs:eleven_v4:turkish"
    assert tts.resolve_voice(profiles, "test-agent", locale="en-GB") == "elevenlabs:eleven_v4:english"
    assert tts.resolve_voice_settings("test-agent", "tr-TR") == {"stability": 0.2, "speed": 0.8}
    assert tts.resolve_voice_settings("test-agent", "en-US") == {"stability": 0.9, "speed": 1.1}
    assert tts.resolve_voice(profiles, "test-agent", "openai:tts-1:echo", locale="tr") == "openai:tts-1:echo"
    monkeypatch.setattr(tts.settings, "tts_locale", "tr-TR")
    assert tts.resolve_voice(profiles, "test-agent") == "elevenlabs:eleven_v4:turkish"
    info = voice_preferences.agent_configs(profiles)["agents"]
    assert len(info) == 1
    assert info[0]["effective_voice"] == "openai:tts-1:nova"
    assert info[0]["languages"]["en"]["effective_voice_settings"]["speed"] == 1.1


def test_partial_edits_and_resets_do_not_destroy_other_language_or_old_pins(profiles):
    save(profiles, voice_id="openai:tts-1:nova", stability=0.6)
    save(profiles, language="tr", voice_id="azure:tr-TR-EmelNeural", stability=0.2)
    save(profiles, language="en", voice_id="openai:tts-1:echo", stability=0.9)
    save(profiles, language="tr", stability=None)
    assert tts.resolve_voice_settings("test-agent", "tr") == {"stability": 0.6}
    save(profiles, language="tr", voice_id=None)
    assert tts.resolve_voice(profiles, "test-agent", locale="tr") == "openai:tts-1:nova"
    save(profiles, language="tr", speed=0.7)
    save(profiles, language="tr")
    assert "tr" not in runtime_state.get_voice_overrides()["test-agent"]["languages"]
    assert tts.resolve_voice(profiles, "test-agent", locale="en") == "openai:tts-1:echo"
    save(profiles)  # Existing mobile reset clears shared settings only.
    assert tts.resolve_voice(profiles, "test-agent", locale="tr") == "openai:tts-1:alloy"
    assert tts.resolve_voice_settings("test-agent", "en") == {"stability": 0.9}


def test_speak_route_passes_locale_to_pin_and_tuning_resolution(profiles, monkeypatch):
    save(profiles, language="tr", voice_id="azure:tr-TR-EmelNeural", stability=0.2)
    save(profiles, language="en", voice_id="openai:tts-1:nova", stability=0.9)
    seen = []

    async def synthesize(text, voice_ref, locale, tuning):
        seen.append((voice_ref, locale, tuning))
        return b"mp3"

    monkeypatch.setattr(tts, "synthesize", synthesize)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(profiles=profiles)))
    for locale in ("tr-TR", "en-US"):
        response = asyncio.run(voice.speak(request, SpeakRequest(text="Test.", agent_id="test-agent", locale=locale)))
        assert response.body == b"mp3"
    assert seen == [("azure:tr-TR-EmelNeural", "tr-TR", {"stability": 0.2}),
                    ("openai:tts-1:nova", "en-US", {"stability": 0.9})]


def test_persistence_failure_does_not_acknowledge_or_change_live_preferences(profiles, monkeypatch):
    save(profiles, language="tr", voice_id="openai:tts-1:nova")
    before = runtime_state._STATE_FILE.read_text()

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(type(runtime_state._STATE_FILE), "replace", fail)
    with pytest.raises(OSError, match="disk full"):
        save(profiles, language="tr", voice_id="openai:tts-1:echo")
    assert tts.resolve_voice(profiles, "test-agent", locale="tr") == "openai:tts-1:nova"
    assert runtime_state._STATE_FILE.read_text() == before


def test_automation_uses_its_language_instead_of_the_system_language(profiles, monkeypatch):
    from app.core import trigger_runner

    save(profiles, language="tr", voice_id="elevenlabs:eleven_v4:turkish", stability=0.2)
    save(profiles, language="en", voice_id="elevenlabs:eleven_v4:english", stability=0.9)
    monkeypatch.setattr(tts.settings, "agent_language", "tr")
    seen = []

    async def prepare(text, **kwargs):
        return text

    async def synthesize(text, voice_ref, **kwargs):
        seen.append((voice_ref, kwargs["locale"], kwargs["voice_settings"]))
        return b"mp3"

    async def deliver(*args, **kwargs):
        return True

    monkeypatch.setattr(tts, "prepare_speech_text", prepare)
    monkeypatch.setattr(tts, "synthesize_prepared", synthesize)
    assert asyncio.run(trigger_runner._deliver_voice(
        agent_id="test-agent", text="Hello.", profile=profiles.get("test-agent"), title="Test",
        telegram_bots=SimpleNamespace(deliver_voice=deliver), request_id="test", language="en",
    ))
    assert seen == [("elevenlabs:eleven_v4:english", "en", {"stability": 0.9})]


@pytest.mark.parametrize("changes", [{"language": "de"}, {"stability": 2}, {"speed": 0.1}, {"unknown": True}])
def test_invalid_preferences_are_rejected(changes):
    with pytest.raises(ValidationError):
        VoiceAgentPatch(**changes)


def test_session_alias_cannot_have_a_separate_voice(profiles):
    with pytest.raises(LookupError):
        voice_preferences.update_agent(profiles, "alias", VoiceAgentPatch(voice_id="openai:tts-1:nova"))
