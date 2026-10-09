"""Owner customization delivery/persistence checks; no live behavioral claims."""
from types import SimpleNamespace
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, BackgroundTasks
from pydantic import ValidationError

from app.config import settings
from app.core import runtime_state
from app.core.context import AgentContext
from app.core.orchestrator import AgentOrchestrator
from app.middleware.auth import AuthMiddleware
from app.profiles.registry import ProfileRegistry
from app.profiles.speda import SPEDAProfile
from app.profiles.atomix import AtomixProfile
from app.profiles.warroom import WarRoomProfile
from app.routers.agents import router
from app.schemas.agent import PersonalitySettings
from app.schemas.chat import ChatRequest


@pytest.fixture
def isolated_personalities(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime_state, "_cache", {})
    monkeypatch.setattr(runtime_state, "_STATE_FILE", tmp_path / "runtime_state.json")
    profiles = ProfileRegistry()
    for profile in (SPEDAProfile(), AtomixProfile(), WarRoomProfile()):
        profiles.register(profile)
    return profiles


async def test_authenticated_settings_are_independent_durable_and_resettable(isolated_personalities, monkeypatch):
    monkeypatch.setattr(settings, "speda_api_key", "fixture-secret")
    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.include_router(router)
    app.state.profiles = isolated_personalities
    headers = {"X-API-Key": "fixture-secret"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://fixture") as client:
        assert (await client.get("/agents/personalities")).status_code == 401
        assert (await client.post("/agents/personalities", json={})).status_code == 401
        saved = await client.post("/agents/personalities", headers=headers, json={"agent_id": "atomix",
            "settings": {"instructions": "Talk like an observant training partner.", "humor": "dry", "tone": "familiar"}})
        assert saved.status_code == 200
        rows = {row["agent_id"]: row for row in saved.json()}
        assert rows["atomix"]["settings"]["humor"] == "dry"
        assert rows["speda"]["settings"]["instructions"] == ""
        assert "warroom" not in rows
        runtime_state._cache = None
        again = await client.get("/agents/personalities", headers=headers)
        assert again.json() == saved.json()
        assert (await client.post("/agents/personalities", headers=headers,
            json={"agent_id": "missing", "settings": {}})).status_code == 404
        assert (await client.post("/agents/personalities", headers=headers,
            json={"agent_id": "speda", "settings": {"humor": "unknown"}})).status_code == 422
        assert (await client.post("/agents/personalities", headers=headers,
            json={"agent_id": "atomix", "settings": {}})).status_code == 200
    assert "atomix" not in runtime_state.get_agent_personalities()


async def test_failed_disk_write_is_not_acknowledged_or_applied(isolated_personalities, monkeypatch):
    runtime_state.set_agent_personality("atomix", {"tone": "familiar"})
    previous = runtime_state.get_agent_personalities()
    original_write = Path.write_text
    def failed_write(path, *args, **kwargs):
        if path.name == "runtime_state.json.tmp":
            raise OSError("disk full")
        return original_write(path, *args, **kwargs)
    monkeypatch.setattr(Path, "write_text", failed_write)
    from app.services.personality import save_personality
    from app.schemas.agent import AgentPersonalitySet
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as failure:
        save_personality(isolated_personalities, AgentPersonalitySet(agent_id="atomix", settings=PersonalitySettings(tone="professional")))
    assert failure.value.status_code == 503
    assert runtime_state.get_agent_personalities() == previous


@pytest.mark.parametrize("trigger", ["user", "n8n", "agent"])
def test_profile_preferences_reach_prompt_on_every_trigger_without_duplication(isolated_personalities, trigger):
    profiles = isolated_personalities
    runtime_state.set_agent_personality("atomix", {"instructions": "Use an observant training partner's perspective.", "humor": "dry"})
    runtime_state.set_agent_personality("speda", {"instructions": "Use a thoughtful executive partner's perspective."})
    context = AgentContext(agent_id="atomix", user_id=1, session_id=1, request_id="fixture", triggered_by=trigger,
        trigger_payload={}, output_mode="respond", model="fixture", system_prompt="", conversation_history=[], db=None,
        custom_instructions="Use shorter paragraphs on this device.")
    engine = AgentOrchestrator(None, None, profiles, None)
    prompt = engine.build_system_prompt(context)
    assert "observant training partner's perspective" in prompt
    assert "thoughtful executive partner's perspective" not in prompt
    assert "Use shorter paragraphs on this device." in prompt
    assert "not a doctor" in prompt.lower() or "not a physician" in prompt.lower()
    assert "authorization" in prompt.lower() and "memory evidence" in prompt.lower()
    context.system_prompt = prompt
    assert engine.build_system_prompt(context) == prompt
    context.agent_id = "warroom"
    assert "thoughtful executive partner's perspective" in engine.build_system_prompt(context)


def test_empty_settings_leave_default_profile_prompt_identical(isolated_personalities):
    context = AgentContext(agent_id="speda", user_id=1, session_id=1, request_id="fixture", triggered_by="user",
        trigger_payload={}, output_mode="respond", model="fixture", system_prompt="", conversation_history=[], db=None)
    from app.services import language
    profile = isolated_personalities.default
    expected = profile.build_system_prompt({"timezone": context.timezone, "model": context.model, "language": language.name_of()})
    assert AgentOrchestrator(None, None, isolated_personalities, None).build_system_prompt(context) == expected
    with pytest.raises(ValidationError):
        PersonalitySettings(instructions="x" * 6001)
    with pytest.raises(ValidationError):
        ChatRequest(message="hello", system_prompt="x" * 6001)


async def test_http_chat_carries_client_instructions_into_detached_engine(isolated_personalities):
    from app.routers.chat import _run_chat
    captured = []
    class Sessions:
        async def get_or_create(self, **kwargs):
            return SimpleNamespace(id=1, project_id=None)
        async def save_message(self, *args):
            pass
        async def load_history(self, *args):
            return [{"role": "user", "content": "Hello"}]
        def get_loaded_servers(self, _):
            return set()
        def get_loaded_tools(self, _):
            return set()
    class Turns:
        def is_live(self, _):
            return False
        def start(self, **kwargs):
            captured.append(kwargs["context"])
            return kwargs["context"].request_id
        async def subscribe(self, _):
            if False:
                yield ""
    state = SimpleNamespace(profiles=isolated_personalities, session_manager=Sessions(), orchestrator=None,
                            turns=Turns(), agent_proxy=None)
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    await _run_chat(request, "speda", ChatRequest(message="Hello", system_prompt="Use plain, conversational explanations."), BackgroundTasks(), None)
    assert captured[0].custom_instructions == "Use plain, conversational explanations."
    assert "Use plain, conversational explanations." in AgentOrchestrator(None, None, isolated_personalities, None).build_system_prompt(captured[0])
