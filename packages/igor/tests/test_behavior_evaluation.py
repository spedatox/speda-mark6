"""Evaluation validity and isolation checks; mock replies are never recovery evidence."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.config import settings
from app.core.registry import CapabilityRegistry
from app.middleware.auth import AuthMiddleware
from app.profiles.registry import ProfileRegistry
from app.profiles.speda import SPEDAProfile
from app.profiles.atomix import AtomixProfile
from app.routers.admin import router
from app.services.behavior_config import behavior_config_snapshot
from app.services.llm_client import LLMClient, TextBlock, ToolUseBlock, Usage
from app.skills.read_skill import ReadSkillSkill
from app.skills.tool_search import ToolSearchSkill


def harness():
    path = Path(__file__).parents[1] / "evals/behavior/run_eval.py"
    spec = importlib.util.spec_from_file_location("behavior_evaluation_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_snapshot_requires_auth_and_excludes_credentials(monkeypatch):
    monkeypatch.setattr(settings, "speda_api_key", "fixture-backend-secret")
    monkeypatch.setattr(settings, "openai_api_key", "fixture-provider-secret")
    registry = CapabilityRegistry()
    await registry.register_skill(ReadSkillSkill())
    profiles = ProfileRegistry()
    profiles.register(SPEDAProfile())
    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.include_router(router)
    app.state.registry, app.state.profiles = registry, profiles
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://fixture") as client:
        assert (await client.get("/admin/evals/behavior-config")).status_code == 401
        assert (await client.get("/admin/evals/behavior-config", headers={"X-API-Key": "wrong"})).status_code == 401
        response = await client.get("/admin/evals/behavior-config", headers={"X-API-Key": "fixture-backend-secret"})
    assert response.status_code == 200
    data = response.json()
    assert data["tool_catalog"][0]["definition"]["name"] == "read_skill"
    assert data["agents"]["speda"]["background_model"]
    assert "fixture-backend-secret" not in response.text
    assert "fixture-provider-secret" not in response.text
    assert not any("api_key" in name for name in data["settings"])


@pytest.mark.parametrize("profile", [SPEDAProfile(), AtomixProfile()])
def test_fixed_instructions_are_bounded_with_canvas_and_memory_boundaries(profile):
    text = profile.build_system_prompt({"timezone": "Europe/Istanbul", "language": "English"})
    assert len(text) <= 35000
    assert "Canvas" in text or "CANVAS" in text
    assert "mandatory" in text.lower()
    assert "sourced observations" in text and "owner testimony" in text
    guide = Path(__file__).parents[1] / "app/skills/skill_docs/inline-rendering/SKILL.md"
    assert "```calendar" in guide.read_text(encoding="utf-8")


def test_unidentified_provider_success_and_substitution_invalidate_comparison():
    runner = harness()
    assert runner.provider_trace_error([{"wire_request": {"model": "fixture"}}])
    assert runner.provider_trace_error([{"wire_request": {"model": "fixture"}, "returned_models": ["other"]}])
    assert runner.provider_trace_error([{"wire_request": {"model": "fixture"}, "error": "Auth", "http_status": 401}])
    assert runner.provider_trace_error([{"wire_request": {"model": "fixture"}, "returned_models": ["fixture"]}]) is None
    assert runner.provider_trace_error([{"wire_request": {"model": "fixture"}, "returned_models": ["fixture"], "dimensions": [3]}], 2)
    assert runner.provider_trace_error([{"wire_request": {"model": "fixture"}, "returned_models": ["fixture"], "dimensions": [3, 2]}])
    with pytest.raises(ValueError):
        runner.fixture_dimensions({"a": [1., 0.], "b": [1., 0., 0.]})


async def test_harness_executes_guides_discloses_schemas_and_persists_actual_replies(monkeypatch, tmp_path):
    runner = harness()
    model = "openai:gpt-6-luna"
    registry = CapabilityRegistry()
    await registry.register_skill(ReadSkillSkill())
    await registry.register_skill(ToolSearchSkill())
    remote = {"name": "fixture_calendar_lookup", "description": "Read fixture availability. Use for calendar lookup. Avoid writes. Returns fixture events.",
              "input_schema": {"type": "object", "properties": {"unique_parameter": {"type": "string"}}}}
    registry._mcp_tool_defs.append(remote)
    registry._mcp_tool_map[remote["name"]] = "fixture_calendar"
    config = runner.local_config(model)
    config.update(production_verified=True, source="UNIT TEST: mock provider; no behavioral claims", tool_catalog=registry.definition_snapshot(),
                  api_by_agent={"speda": "responses"}, agents={"speda": {"background_model": SPEDAProfile().background_model(model)}})
    config["runtime"]["agent_models"] = {"speda": model}
    config["settings"].update(dead_zone_mode="off", lazy_tools=True)
    fixture = {"cases": [{"id": "persisted", "agent": "speda", "index_history": False, "post_turn": False,
        "rubric": "Unit test of runner mechanics only", "turns": [
            {"message": "Please use concise explanations.", "client_context": {"platform": "desktop"}},
            {"message": "What did you just agree to?"},
            {"message": "What did we agree to?", "new_session": True}]}]}
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps(fixture), encoding="utf-8")
    monkeypatch.setattr(runner, "credential_present", lambda _: True)
    async def offline(texts):
        raise ConnectionError("fixture outage")
    monkeypatch.setattr("app.services.embeddings.embed_texts", offline)
    blocks = [ToolUseBlock("search-1", "tool_search", {"query": "fixture_calendar_lookup"}),
              ToolUseBlock("guide-1", "read_skill", {"skill_name": "inline-rendering"}),
              TextBlock("I will use concise explanations."), TextBlock("Yes, concise."), TextBlock("Concise, as we agreed.")]
    calls = []
    class Handle:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        @property
        def text_stream(self):
            async def text():
                if self.block.type == "text":
                    yield self.block.text
            return text()
        async def get_final_message(self):
            return SimpleNamespace(content=[self.block], model="gpt-6-luna", usage=Usage(),
                stop_reason="tool_use" if self.block.type == "tool_use" else "end_turn")
    def stream(client, **kwargs):
        calls.append(runner.copy.deepcopy(kwargs))
        handle = Handle()
        handle.block = blocks[len(calls) - 1]
        return handle
    monkeypatch.setattr(LLMClient, "stream_message", stream)
    report = await runner.run_cases(model, True, [], config, cases_path=cases)
    turns = report["results"][0]["turns"]
    assert all(turn["status"] == "completed" for turn in turns)
    assert remote["name"] not in [t["name"] for t in calls[0]["tools"]]
    assert remote == next(t for t in calls[1]["tools"] if t["name"] == remote["name"])
    assert remote == next(t for t in calls[3]["tools"] if t["name"] == remote["name"])
    tool_result = turns[0]["recall"]["fixture_tool_calls"][0]["result"]
    assert remote["name"] in tool_result and "unique_parameter" not in tool_result
    assert "```calendar" in turns[0]["recall"]["fixture_tool_calls"][1]["result"]
    assert "I will use concise explanations." in str(calls[3]["messages"])
    assert "I will use concise explanations." in str(calls[4]["system"])
    assert "desktop app" in str(calls[0]["messages"])
