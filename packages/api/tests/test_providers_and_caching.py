"""
Provider-parity + prompt-caching regression tests.

Covers the token-optimization architecture:
  - mixed-TTL cache breakpoints (1h prefix / 5m conversation, Anthropic ordering rule)
  - byte-stable per-message timestamps (the system prompt carries no clock)
  - Anthropic-format → chat-completions translation (OpenAI/Gemini/Ollama)
  - provider-aware background model selection
  - Dead Zone Protocol tool filtering + hallucinated-tool feedback
"""

from datetime import datetime
from types import SimpleNamespace

import pytest

from app.config import settings
from app.core.registry import CapabilityRegistry
from app.core.session_manager import SessionManager
from app.profiles.speda import SPEDAProfile
from app.services.anthropic_client import _apply_prompt_caching
from app.services.llm_client import (
    _FINISH_TO_STOP,
    _to_openai_params,
    _translate_message,
    parse_model_ref,
)
from app.skills.base import Skill


# ── Prompt caching ────────────────────────────────────────────────────────────


def test_mixed_ttl_breakpoints():
    out = _apply_prompt_caching({
        "model": "claude-sonnet-4-6",
        "system": [
            {"type": "text", "text": "stable core", "_cache": True},
            {"type": "text", "text": "memory", "_cache": True},
        ],
        "tools": [{"name": "a"}, {"name": "b"}],
        "messages": [
            {"role": "user", "content": "[2026-06-11 19:00 UTC] hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "[2026-06-11 19:01 UTC] again"},
        ],
    })

    # Prefix (tools + system) carries the long TTL…
    assert out["tools"][-1]["cache_control"]["ttl"] == settings.prompt_cache_ttl
    assert all(b["cache_control"]["ttl"] == settings.prompt_cache_ttl for b in out["system"])
    # …the conversation breakpoint the short one (1h before 5m = valid ordering).
    last_block = out["messages"][-1]["content"][-1]
    assert last_block["cache_control"]["ttl"] == settings.prompt_cache_conversation_ttl
    # `_cache` markers must never reach the API.
    assert all("_cache" not in b for b in out["system"])
    # Breakpoint budget: tools(1) + system(2) + conversation(1) = 4 (the max).
    n_breakpoints = (
        sum(1 for t in out["tools"] if "cache_control" in t)
        + sum(1 for b in out["system"] if "cache_control" in b)
        + 1
    )
    assert n_breakpoints <= 4


def test_only_last_message_gets_breakpoint():
    out = _apply_prompt_caching({
        "messages": [
            {"role": "user", "content": "one"},
            {"role": "user", "content": "two"},
        ],
    })
    assert "cache_control" not in str(out["messages"][0])
    assert out["messages"][-1]["content"][-1]["cache_control"]


# ── Timestamp stamping (byte-stable history) ─────────────────────────────────


def test_stamp_is_deterministic():
    ts = datetime(2026, 6, 11, 19, 40, 23)  # seconds must NOT leak into the stamp
    a = SessionManager.stamp_user_content("what time is it", ts)
    b = SessionManager.stamp_user_content("what time is it", ts)
    assert a == b == "[2026-06-11 19:40 UTC] what time is it"


def test_stamp_list_content_prepends_text_block():
    ts = datetime(2026, 6, 11, 8, 5, 0)
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "x"}}]
    stamped = SessionManager.stamp_user_content(content, ts)
    assert stamped[0] == {"type": "text", "text": "[2026-06-11 08:05 UTC]"}
    assert stamped[1]["type"] == "image"


# ── Model-ref routing ────────────────────────────────────────────────────────


def test_parse_model_ref():
    assert parse_model_ref("claude-sonnet-4-6") == ("anthropic", "claude-sonnet-4-6")
    assert parse_model_ref("openai:gpt-5-mini") == ("openai", "gpt-5-mini")
    assert parse_model_ref("zai:glm-4.6") == ("zai", "glm-4.6")
    assert parse_model_ref("deepseek:deepseek-v4-pro") == ("deepseek", "deepseek-v4-pro")
    # Ollama tags contain colons — only the first segment routes.
    assert parse_model_ref("ollama:llama3.1:8b") == ("ollama", "llama3.1:8b")
    # Unknown prefix falls through to Anthropic untouched.
    assert parse_model_ref("weird:thing") == ("anthropic", "weird:thing")


def test_background_model_follows_active_provider():
    p = SPEDAProfile()
    assert p.background_model("claude-sonnet-4-6") == (
        settings.llm_background_model or p.haiku_model
    )
    assert p.background_model("openai:gpt-5.1") == "openai:gpt-5-mini"
    assert p.background_model("gemini:gemini-2.5-pro") == "gemini:gemini-2.5-flash"
    assert p.background_model("zai:glm-4.6") == "zai:glm-4.5-air"
    assert p.background_model("deepseek:deepseek-v4-pro") == "deepseek:deepseek-v4-flash"
    # Dead Zone: the local model is the only one that exists.
    assert p.background_model("ollama:llama3.1:8b") == "ollama:llama3.1:8b"


# ── Chat-completions translation (OpenAI / Gemini / Ollama parity) ──────────


def test_translate_tool_roundtrip_adjacency():
    assistant = {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "checking"},
            {"type": "tool_use", "id": "call_1", "name": "get_time", "input": {}},
            {"type": "tool_use", "id": "call_2", "name": "search", "input": {"q": "x"}},
        ],
    }
    follow_up = {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "call_1", "content": "19:40"},
            {"type": "tool_result", "tool_use_id": "call_2", "content": [{"type": "text", "text": "ok"}]},
        ],
    }
    a = _translate_message(assistant)
    assert len(a) == 1 and len(a[0]["tool_calls"]) == 2  # parallel calls preserved
    f = _translate_message(follow_up)
    # tool messages must directly follow the assistant tool_calls message
    assert [m["role"] for m in f] == ["tool", "tool"]
    assert f[0]["tool_call_id"] == "call_1" and f[1]["content"] == "ok"


def test_assistant_tool_call_content_never_null():
    # z.ai GLM rejects `content: null` (error 1214), so an assistant message
    # that is purely a tool call must serialize with an empty-string content,
    # not None — otherwise the agent loop breaks the first time any tool runs.
    assistant_tool_only = {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": "call_1", "name": "memory", "input": {}}],
    }
    out = _translate_message(assistant_tool_only)
    assert len(out) == 1
    assert out[0]["content"] == ""
    assert out[0]["content"] is not None
    assert out[0]["tool_calls"][0]["id"] == "call_1"
    # A text+tool_use assistant message keeps its text as content.
    with_text = {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "one moment"},
            {"type": "tool_use", "id": "call_2", "name": "memory", "input": {}},
        ],
    }
    assert _translate_message(with_text)[0]["content"] == "one moment"
    # An assistant message with neither text nor tools produces nothing.
    assert _translate_message({"role": "assistant", "content": []}) == []


def test_to_openai_params_system_join_and_max_tokens():
    kwargs = {
        "system": [
            {"type": "text", "text": "core", "_cache": True},
            {"type": "text", "text": "memory"},
        ],
        "messages": [{"role": "user", "content": "[2026-06-11 19:40 UTC] hi"}],
        "max_tokens": 1024,
    }
    p_openai = _to_openai_params("openai", "gpt-5-mini", kwargs)
    assert p_openai["messages"][0] == {"role": "system", "content": "core\n\nmemory"}
    assert p_openai["max_completion_tokens"] == 1024 and "max_tokens" not in p_openai
    p_ollama = _to_openai_params("ollama", "llama3.1:8b", kwargs)
    assert p_ollama["max_tokens"] == 1024


def test_zai_thinking_toggle_maps_from_reasoning_effort():
    kwargs = {
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 512,
    }
    # Low/minimal effort → GLM thinking disabled (so short background tasks
    # don't burn the budget on hidden reasoning and return empty content).
    p_low = _to_openai_params("zai", "glm-4.5-air", {**kwargs, "reasoning_effort": "minimal"})
    assert p_low["extra_body"] == {"thinking": {"type": "disabled"}}
    # reasoning_effort must NEVER leak through as a raw param on the zai path:
    # GLM-4.x has no such field and GLM-5.2's rejects "minimal" (only high/max).
    assert "reasoning_effort" not in p_low
    # Flagship GLM-5.2 takes the same disabled-thinking route on a low hint.
    p_flagship = _to_openai_params("zai", "glm-5.2", {**kwargs, "reasoning_effort": "minimal"})
    assert p_flagship["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in p_flagship
    # No hint → default (thinking on), so interactive chat keeps full reasoning.
    p_default = _to_openai_params("zai", "glm-5.2", kwargs)
    assert "extra_body" not in p_default
    # GLM speaks OpenAI chat-completions max_tokens (not max_completion_tokens).
    assert p_default["max_tokens"] == 512


def test_deepseek_forces_non_thinking_when_tools_present():
    tool = {"name": "get_time", "description": "x", "input_schema": {"type": "object"}}
    kwargs = {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 512}

    # Tools present (the whole agent loop) → non-thinking, because V4 thinking
    # mode rejects tool_choice and requires reasoning_content round-tripping.
    p_tools = _to_openai_params("deepseek", "deepseek-v4-pro", {**kwargs, "tools": [tool]})
    assert p_tools["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in p_tools

    # Low/minimal background hint (no tools) → also non-thinking, so a short
    # task isn't starved. "minimal" is invalid for V4 and must not be forwarded.
    p_bg = _to_openai_params("deepseek", "deepseek-v4-flash", {**kwargs, "reasoning_effort": "minimal"})
    assert p_bg["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in p_bg

    # Genuine high effort on a TOOL-FREE call keeps thinking on (forwarded).
    p_reason = _to_openai_params("deepseek", "deepseek-v4-pro", {**kwargs, "reasoning_effort": "high"})
    assert "extra_body" not in p_reason
    assert p_reason["reasoning_effort"] == "high"
    # But add tools back and thinking is disabled even with a high hint.
    p_reason_tools = _to_openai_params(
        "deepseek", "deepseek-v4-pro", {**kwargs, "reasoning_effort": "high", "tools": [tool]}
    )
    assert p_reason_tools["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in p_reason_tools


def test_finish_reason_mapping():
    assert _FINISH_TO_STOP["stop"] == "end_turn"
    assert _FINISH_TO_STOP["tool_calls"] == "tool_use"
    assert _FINISH_TO_STOP["length"] == "max_tokens"


# ── Dead Zone Protocol + hallucinated tools ──────────────────────────────────


class _OfflineSkill(Skill):
    name = "local_thing"
    description = "Local. Works offline. Returns text. Used in tests only."
    input_schema = {"type": "object", "properties": {}}

    async def execute(self, args, context):  # pragma: no cover
        return "ok"


class _OnlineSkill(_OfflineSkill):
    name = "web_thing"
    requires_network = True


@pytest.fixture
async def registry():
    r = CapabilityRegistry()
    r.register_task_tool()
    await r.register_skill(_OfflineSkill())
    await r.register_skill(_OnlineSkill())
    return r


async def test_dead_zone_filters_online_capabilities(registry, monkeypatch):
    from app.core import runtime_state
    monkeypatch.setattr(runtime_state, "get_budget_mode", lambda: False)

    names_online = {t["name"] for t in registry.list_tools()}
    assert {"local_thing", "web_thing"} <= names_online

    names_dz = {t["name"] for t in registry.list_tools(offline_only=True)}
    assert "local_thing" in names_dz
    assert "web_thing" not in names_dz
    assert "Task" not in names_dz  # sub-agents need an uplink


async def test_dead_zone_mode_forced(registry, monkeypatch):
    monkeypatch.setattr(settings, "dead_zone_mode", "on")
    assert await registry.dead_zone_active() is True
    monkeypatch.setattr(settings, "dead_zone_mode", "off")
    assert await registry.dead_zone_active() is False


async def test_unknown_tool_returns_corrective_feedback(registry):
    ctx = SimpleNamespace(request_id="test-req")
    result = await registry.execute("search_the_web_for_hello", {}, ctx)
    assert "does not exist" in result
    assert "local_thing" in result  # the model is told what IS available


async def test_skills_bypass_agent_allowlist(registry):
    # When tool_allowlist is None (all profiles now), every tool is returned.
    tools = registry.list_tools(allowlist=None)
    names = {t["name"] for t in tools}
    assert "local_thing" in names
    assert "web_thing" in names


async def test_allowlist_filter_still_works_when_set(registry):
    # The allowlist mechanism is intact for potential future re-narrowing.
    tools = registry.list_tools(allowlist={"local_thing"})
    names = {t["name"] for t in tools}
    assert "local_thing" in names
    # web_thing and Task are not in the allowlist, so they're filtered.
    assert "web_thing" not in names
    assert "Task" not in names
    # Runtime infra (memory/read_skill/use_toolset) always pass.


async def test_available_models_dynamic_fetch(monkeypatch):
    from app.services.llm_client import available_models
    import app.services.llm_client as llm_client

    # Force OpenAI key settings
    monkeypatch.setattr(settings, "openai_api_key", "mock-key")
    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "anthropic_api_key", "")

    # Mock OpenAI client
    class MockModel:
        def __init__(self, id_):
            self.id = id_

    class MockModelsRes:
        def __init__(self, ids):
            self.data = [MockModel(i) for i in ids]

    class MockModelsAPI:
        def __init__(self, ids):
            self._ids = ids
        async def list(self):
            return MockModelsRes(self._ids)

    class MockAsyncOpenAI:
        def __init__(self, **kwargs):
            self.models = MockModelsAPI(["gpt-4o", "gpt-4o-mini", "whisper-1"])

    import openai
    monkeypatch.setattr(openai, "AsyncOpenAI", MockAsyncOpenAI)

    models = await available_models()
    names = {m["id"] for m in models}
    # gpt-4o and gpt-4o-mini should be present, whisper-1 should be filtered out
    assert "openai:gpt-4o" in names
    assert "openai:gpt-4o-mini" in names
    assert "openai:whisper-1" not in names



