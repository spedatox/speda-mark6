# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Provider-parity + prompt-caching regression tests.

Covers the token-optimization architecture:
  - mixed-TTL cache breakpoints (1h prefix / 5m conversation, Anthropic ordering rule)
  - byte-stable per-message timestamps (the system prompt carries no clock)
  - Anthropic-format → chat-completions translation (OpenAI/Gemini/Ollama)
  - provider-aware background model selection
  - Dead Zone Protocol tool filtering + hallucinated-tool feedback
"""

import json
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
    _OpenAICompatStream,
    _responses_to_message,
    _stop_reason_for,
    _thought_signature,
    _usage_from,
    blocks_to_dicts,
    supports_vision,
    TextBlock,
    ToolUseBlock,
    _to_openai_params,
    _to_responses_params,
    _translate_message,
    _translate_message_responses,
    _use_responses_api,
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
    # created_at is stored UTC and rendered in the OWNER's zone (+03), because
    # this stamp is how the agent knows what time it is — a UTC stamp had every
    # agent answering "what time is it" three hours early. Byte-stability, the
    # property that protects the prompt cache, is unaffected: same instant plus
    # same zone is always the same string.
    assert a == b == "[Thu 2026-06-11 22:40 +03] what time is it"


def test_stamp_names_the_weekday():
    """The weekday is stated, never left for the model to compute.

    Asked only for "2026-08-04" a model routinely names the wrong day and then
    reasons confidently from it ("that's a Monday, so the weekend is…"). The day
    is a fact we hold; three tokens spend it. 2026-08-04 is a TUESDAY.
    """
    assert SessionManager.stamp_user_content("", datetime(2026, 8, 4, 9, 0)).startswith("[Tue ")
    # And it must be the OWNER's day, not UTC's: 22:30 UTC is already tomorrow
    # in Istanbul, so the weekday has to roll with the local date.
    assert SessionManager.stamp_user_content("", datetime(2026, 8, 4, 22, 30)).startswith("[Wed ")


def test_stamp_list_content_prepends_text_block():
    ts = datetime(2026, 6, 11, 8, 5, 0)
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "x"}}]
    stamped = SessionManager.stamp_user_content(content, ts)
    assert stamped[0] == {"type": "text", "text": "[Thu 2026-06-11 11:05 +03]"}
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


@pytest.fixture
def unpinned(monkeypatch):
    """No routing-matrix pins and no .env model overrides — the profile's own
    policy, isolated from whatever the dev machine has configured."""
    monkeypatch.setattr("app.core.runtime_state.get_agent_models", lambda: {})
    monkeypatch.setattr(settings, "llm_background_model", "")
    monkeypatch.setattr(settings, "llm_main_model", "")


def test_background_model_follows_active_provider(unpinned):
    p = SPEDAProfile()
    assert p.background_model("claude-sonnet-4-6") == p.haiku_model
    assert p.background_model("openai:gpt-5.1") == "openai:gpt-5-mini"
    assert p.background_model("gemini:gemini-2.5-pro") == "gemini:gemini-3.5-flash-lite"
    assert p.background_model("zai:glm-4.6") == "zai:glm-4.5-air"
    assert p.background_model("deepseek:deepseek-v4-pro") == "deepseek:deepseek-v4-flash"
    # Dead Zone: the local model is the only one that exists.
    assert p.background_model("ollama:llama3.1:8b") == "ollama:llama3.1:8b"


# ── Vision routing ───────────────────────────────────────────────────────────
# An image sent to a text-only model is not a degraded answer — the provider
# rejects the whole request, and keeps rejecting it, because the image stays in
# the history. The reroute must fix that WITHOUT ever changing provider.


def test_supports_vision_knows_which_models_read_images():
    # Whole-line multimodal providers need no per-id knowledge.
    assert supports_vision("claude-sonnet-4-6")
    assert supports_vision("gemini:gemini-2.5-flash")
    assert supports_vision("openai:gpt-5-mini")
    assert not supports_vision("openai:gpt-3.5-turbo")
    # DeepSeek serves images on exactly one id.
    assert supports_vision("deepseek:deepseek-v4-flash-vision-exp")
    assert not supports_vision("deepseek:deepseek-v4-pro")
    assert not supports_vision("deepseek:deepseek-v4-flash")
    # Ollama depends entirely on which weights were pulled.
    assert supports_vision("ollama:llava:13b")
    assert not supports_vision("ollama:llama3.1:8b")


def test_vision_tier_stays_on_the_owners_provider():
    p = SPEDAProfile()
    # Text-only DeepSeek → DeepSeek's vision model, same provider.
    assert p.vision_tier("deepseek:deepseek-v4-pro") == "deepseek:deepseek-v4-flash-vision-exp"
    # Already sees images → untouched (no needless downgrade).
    assert p.vision_tier("claude-sonnet-4-6") == "claude-sonnet-4-6"
    assert p.vision_tier("deepseek:deepseek-v4-flash-vision-exp") == "deepseek:deepseek-v4-flash-vision-exp"
    # No vision model on that provider → the pin is kept and fails as itself.
    # An image must never be the thing that moves a turn onto Anthropic.
    for ref in ("zai:glm-4.6", "ollama:llama3.1:8b", "nvidia:meta/llama-3.1-8b-instruct"):
        assert p.vision_tier(ref) == ref


def test_an_image_is_found_anywhere_in_the_history():
    from app.core.orchestrator import _carries_image

    image = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "x"}}
    assert not _carries_image([{"role": "user", "content": "hello"}])
    assert not _carries_image([{"role": "user", "content": [{"type": "text", "text": "hello"}]}])
    # Turn 1 sent the screenshot; turn 9 still carries it, and still cannot be
    # sent to a text-only model.
    history = [
        {"role": "user", "content": [image, {"type": "text", "text": "what is this"}]},
        {"role": "assistant", "content": "a chart"},
        {"role": "user", "content": [{"type": "text", "text": "and the y axis?"}]},
    ]
    assert _carries_image(history)


# ── The routing matrix is the authority ──────────────────────────────────────
# Every case below is a way the engine used to reach for Anthropic on its own.
# It may only ever run what the owner picked, on the provider they picked.


def test_a_pinned_agent_runs_its_pin_on_every_trigger_source(unpinned, monkeypatch):
    monkeypatch.setattr(
        "app.core.runtime_state.get_agent_models", lambda: {"speda": "zai:glm-4.6"}
    )
    p = SPEDAProfile()
    for source in ("user", "n8n", "agent"):
        assert p.allocate_model(source) == "zai:glm-4.6"
    assert p.allocate_model("n8n", is_background=True) == "zai:glm-4.6"


def test_background_work_for_a_pinned_agent_stays_on_the_pinned_provider(
    unpinned, monkeypatch
):
    """The bug: background_model keyed off a hardcoded provider tuple, so an
    agent routed outside that list had every title, recap and Legion pre-filter
    silently billed to Anthropic. End to end from the pin, nothing crosses."""
    monkeypatch.setattr(
        "app.core.runtime_state.get_agent_models", lambda: {"speda": "zai:glm-4.6"}
    )
    p = SPEDAProfile()
    for source in ("user", "n8n", "agent"):
        assert p.background_model(p.allocate_model(source)) == "zai:glm-4.5-air"


def test_a_provider_with_no_cheap_tier_keeps_the_model_it_was_given(unpinned):
    # Ollama declares no cheap tier (the local model is the only one there) —
    # it stands rather than degrading to a hosted Anthropic model.
    p = SPEDAProfile()
    assert p.background_model("ollama:qwen3:14b") == "ollama:qwen3:14b"


def test_a_per_turn_model_pick_is_not_overridden_by_the_agent_pin(unpinned, monkeypatch):
    """The composer's per-turn pick is MORE specific than the matrix pin, so
    background work follows the model the turn actually ran on."""
    monkeypatch.setattr(
        "app.core.runtime_state.get_agent_models", lambda: {"speda": "zai:glm-4.6"}
    )
    p = SPEDAProfile()
    assert p.background_model("openai:gpt-5.1") == "openai:gpt-5-mini"


def test_an_unrecognised_prefix_is_never_rewritten_into_an_anthropic_call(unpinned):
    # parse_model_ref reports an unknown prefix as Anthropic; the cheap tier must
    # NOT take that as licence to substitute Haiku for the owner's ref.
    p = SPEDAProfile()
    assert p.cheap_tier("groq:llama-3.3") == "groq:llama-3.3"
    assert p.background_model("groq:llama-3.3") == "groq:llama-3.3"


def test_an_unpinned_automated_turn_stays_on_the_deployments_provider(
    unpinned, monkeypatch
):
    """A deployment whose main model is zai must not have its n8n turns land on
    Anthropic Haiku just because no background model was configured."""
    monkeypatch.setattr(settings, "llm_main_model", "zai:glm-4.6")
    p = SPEDAProfile()
    assert p.allocate_model("user") == "zai:glm-4.6"
    assert p.allocate_model("n8n") == "zai:glm-4.5-air"
    assert p.allocate_model("agent") == "zai:glm-4.5-air"


def test_env_background_override_still_wins_when_nothing_is_pinned(
    unpinned, monkeypatch
):
    monkeypatch.setattr(settings, "llm_background_model", "openai:gpt-5-mini")
    p = SPEDAProfile()
    assert p.allocate_model("n8n") == "openai:gpt-5-mini"
    assert p.background_model("claude-sonnet-4-6") == "openai:gpt-5-mini"


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


def test_openai_reasoning_effort_rides_extra_body():
    # reasoning_effort must go through extra_body, never as a top-level param —
    # the pinned openai SDK rejects the typed kwarg (TypeError before the request
    # even leaves the process).
    tool = {"name": "get_time", "description": "x", "input_schema": {"type": "object"}}
    kwargs = {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 512}

    # Tool-free reasoning call passes the requested effort via extra_body.
    p_reason = _to_openai_params("openai", "gpt-5.1", {**kwargs, "reasoning_effort": "high"})
    assert "reasoning_effort" not in p_reason  # never top-level
    assert p_reason.get("extra_body", {}).get("reasoning_effort") == "high"


def test_gpt56_tool_calls_route_to_responses_api():
    # gpt-5.6+ cannot carry function tools on /v1/chat/completions — post-GA the
    # old reasoning_effort:"none" workaround was honoured only intermittently,
    # which produced the every-other-message 401 "insufficient permissions".
    # Tool calls must go to /v1/responses instead.
    tool = {"name": "get_time", "description": "x", "input_schema": {"type": "object"}}
    kwargs = {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 512, "tools": [tool]}
    for model in ("gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.7"):
        assert _use_responses_api("openai", model, kwargs), model
        # …and the dead workaround must never come back.
        assert "none" != _to_openai_params("openai", model, kwargs).get(
            "extra_body", {}
        ).get("reasoning_effort"), model
    # Tool-FREE 5.6 calls (title/recap generation) stay on chat-completions.
    assert not _use_responses_api("openai", "gpt-5.6-terra", {"messages": []})
    # Other providers are never routed to OpenAI's endpoint.
    assert not _use_responses_api("gemini", "gemini-2.5-pro", kwargs)
    assert not _use_responses_api("openai", "gpt-5-mini", kwargs)


def test_responses_params_shape():
    # Flat tool declarations, instructions instead of a system message, and
    # max_output_tokens instead of max_tokens.
    tool = {"name": "get_time", "description": "x", "input_schema": {"type": "object"}}
    p = _to_responses_params(
        "gpt-5.6-terra",
        {
            "system": [{"type": "text", "text": "you are igor", "_cache": True}],
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 512,
            "tools": [tool],
        },
    )
    assert p["instructions"] == "you are igor"
    assert p["max_output_tokens"] == 512
    assert "max_tokens" not in p and "messages" not in p
    assert p["tools"] == [
        {"type": "function", "name": "get_time", "description": "x", "parameters": {"type": "object"}}
    ]
    assert p["input"] == [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}]
    # reasoning_effort "none" is not a valid Responses effort — never forwarded.
    p_none = _to_responses_params("gpt-5.6-terra", {"messages": [], "reasoning_effort": "none"})
    assert "reasoning" not in p_none


def test_responses_tool_roundtrip_pairs_on_call_id():
    # tool_use → function_call and tool_result → function_call_output, paired by
    # call_id and emitted as TOP-LEVEL items (not nested in role messages).
    assistant = _translate_message_responses(
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "checking"},
                {"type": "tool_use", "id": "call_1", "name": "get_time", "input": {"tz": "UTC"}},
            ],
        }
    )
    assert assistant[0] == {
        "role": "assistant",
        "content": [{"type": "output_text", "text": "checking"}],
    }
    assert assistant[1]["type"] == "function_call"
    assert assistant[1]["call_id"] == "call_1"
    assert json.loads(assistant[1]["arguments"]) == {"tz": "UTC"}

    user = _translate_message_responses(
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call_1", "content": "19:00"},
                {"type": "text", "text": "thanks"},
            ],
        }
    )
    # Result first, so it stays adjacent to the call it answers.
    assert user[0] == {"type": "function_call_output", "call_id": "call_1", "output": "19:00"}
    assert user[1]["content"] == [{"type": "input_text", "text": "thanks"}]


def test_responses_output_parsing():
    resp = SimpleNamespace(
        output=[
            SimpleNamespace(
                type="message",
                content=[SimpleNamespace(type="output_text", text="on it")],
            ),
            SimpleNamespace(
                type="reasoning", summary=[]  # hidden reasoning items must be ignored
            ),
            SimpleNamespace(
                type="function_call",
                id="fc_abc",
                call_id="call_9",
                name="get_time",
                arguments='{"tz":"UTC"}',
            ),
        ],
        incomplete_details=None,
        usage=SimpleNamespace(
            input_tokens=100,
            output_tokens=20,
            input_tokens_details=SimpleNamespace(cached_tokens=64),
        ),
    )
    msg = _responses_to_message(resp)
    assert msg.stop_reason == "tool_use"
    assert [b.type for b in msg.content] == ["text", "tool_use"]
    assert msg.content[0].text == "on it"
    # The id must be call_id — item.id cannot be paired against by the API.
    assert msg.content[1].id == "call_9"
    assert msg.content[1].input == {"tz": "UTC"}
    # input_tokens is what was billed at FULL rate, matching Anthropic's
    # convention — the provider reports 100 INCLUSIVE of its 64 cached, so the
    # cached span is subtracted rather than counted twice. The orchestrator sums
    # input + cache_read into "what the model read", which is 100 again.
    assert msg.usage.input_tokens == 36
    assert msg.usage.cache_read_input_tokens == 64
    assert msg.usage.input_tokens + msg.usage.cache_read_input_tokens == 100

    truncated = SimpleNamespace(
        output=[SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text", text="par")])],
        incomplete_details=SimpleNamespace(reason="max_output_tokens"),
        usage=None,
    )
    assert _responses_to_message(truncated).stop_reason == "max_tokens"


def test_older_gpt5_never_forced_to_none_with_tools():
    # gpt-5 / mini / nano REJECT the value "none" and don't block tools, so no
    # reasoning_effort must be injected when tools are present.
    tool = {"name": "get_time", "description": "x", "input_schema": {"type": "object"}}
    kwargs = {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 512, "tools": [tool]}
    for model in ("gpt-5", "gpt-5-mini", "gpt-5-nano"):
        p = _to_openai_params("openai", model, kwargs)
        assert "none" != p.get("extra_body", {}).get("reasoning_effort"), model
        assert "reasoning_effort" not in p, model  # not top-level either


def test_usage_counts_hidden_reasoning_as_output():
    # Gemini 2.5/3.x report prompt=8 completion=7 total=204 — the 189-token gap
    # is thinking, which is BILLED as output. Verified live against
    # gemini-2.5-flash. Taking completion_tokens at face value under-reports
    # the turn's real output cost by ~30x.
    u = _usage_from(SimpleNamespace(prompt_tokens=8, completion_tokens=7,
                                    total_tokens=204, prompt_tokens_details=None))
    assert u.input_tokens == 8
    assert u.output_tokens == 196

    # A provider whose total is just prompt+completion is left alone.
    u = _usage_from(SimpleNamespace(prompt_tokens=100, completion_tokens=40,
                                    total_tokens=140, prompt_tokens_details=None))
    assert (u.input_tokens, u.output_tokens) == (100, 40)

    # Missing/zero total (some providers omit it) must not zero the output.
    u = _usage_from(SimpleNamespace(prompt_tokens=100, completion_tokens=40,
                                    total_tokens=0, prompt_tokens_details=None))
    assert (u.input_tokens, u.output_tokens) == (100, 40)

    assert _usage_from(None).input_tokens == 0


def test_usage_extracts_zai_cached_tokens():
    # z.ai (like OpenAI/Gemini) reports cache hits under
    # usage.prompt_tokens_details.cached_tokens, and prompt_tokens INCLUSIVE of
    # that span (docs.z.ai/guides/capabilities/cache) — same convention as the
    # Responses API path in test_responses_output_parsing. input_tokens must be
    # the FULL-RATE remainder, not the inclusive total, or every cache hit would
    # double-count against cache_read_input_tokens.
    u = _usage_from(SimpleNamespace(
        prompt_tokens=1200,
        completion_tokens=300,
        total_tokens=1500,
        prompt_tokens_details=SimpleNamespace(cached_tokens=800),
    ))
    assert u.cache_read_input_tokens == 800
    assert u.input_tokens == 400
    assert u.input_tokens + u.cache_read_input_tokens == 1200

    # A cold turn (no cache hit yet) must not be treated as a 0-token miss —
    # cached_tokens=0 is valid and simply means nothing was reused.
    u_cold = _usage_from(SimpleNamespace(
        prompt_tokens=1200,
        completion_tokens=300,
        total_tokens=1500,
        prompt_tokens_details=SimpleNamespace(cached_tokens=0),
    ))
    assert u_cold.cache_read_input_tokens == 0
    assert u_cold.input_tokens == 1200

    # DeepSeek's own field name is the fallback and must still work when
    # prompt_tokens_details is absent entirely (DeepSeek reports the hit count
    # directly on the usage object, not nested).
    u_deepseek = _usage_from(SimpleNamespace(
        prompt_tokens=500,
        completion_tokens=50,
        total_tokens=550,
        prompt_tokens_details=None,
        prompt_cache_hit_tokens=300,
    ))
    assert u_deepseek.cache_read_input_tokens == 300
    assert u_deepseek.input_tokens == 200


async def test_stream_asks_every_provider_for_usage():
    # Usage is only reported on a stream when it is explicitly requested — and
    # Gemini, which used to reject the parameter, now accepts it. Skipping it
    # there left every Gemini turn reporting zero tokens.
    for provider in ("openai", "gemini", "zai", "deepseek", "ollama"):
        s = _OpenAICompatStream(None, {"model": "m"}, provider)
        captured = {}

        class _Client:
            class chat:
                class completions:
                    @staticmethod
                    async def create(**kw):
                        captured.update(kw)
                        return None

        s._client = _Client()
        await s.open()
        assert captured.get("stream_options") == {"include_usage": True}, provider
        assert captured.get("stream") is True, provider


def test_finish_reason_mapping():
    assert _FINISH_TO_STOP["stop"] == "end_turn"
    assert _FINISH_TO_STOP["tool_calls"] == "tool_use"
    assert _FINISH_TO_STOP["length"] == "max_tokens"


def test_tool_blocks_outrank_finish_reason():
    # Gemini's compat bridge reports "stop" (or nothing at all) on a response
    # that carries tool_calls. Mapped verbatim that ends the agentic loop at the
    # moment the model asked for a tool — the tool never runs and the turn is
    # left with an empty answer. The blocks decide.
    calls = [ToolUseBlock(id="call_1", name="get_time", input={})]
    assert _stop_reason_for("stop", calls) == "tool_use"
    assert _stop_reason_for(None, calls) == "tool_use"
    assert _stop_reason_for("STOP", calls) == "tool_use"
    # Without tool blocks the reported reason still rules, case-insensitively.
    assert _stop_reason_for("stop", [TextBlock(text="hi")]) == "end_turn"
    assert _stop_reason_for("STOP", []) == "end_turn"
    assert _stop_reason_for("length", [TextBlock(text="hi")]) == "max_tokens"


# ── Gemini thought signatures ────────────────────────────────────────────────
# Gemini 3.x signs each functionCall part and REQUIRES the signature back on the
# request carrying the tool result; without it the follow-up dies with 400
# INVALID_ARGUMENT and the turn never gets to answer. Verified against the live
# gemini-3.6-flash endpoint. https://ai.google.dev/gemini-api/docs/thinking#signatures


def _signed_call(sig, *, name="get_time", as_extra_model=False):
    """A returned tool call shaped like Gemini's, where the signature rides a
    field the OpenAI SDK has no model for."""
    fn = SimpleNamespace(name=name, arguments='{"tz":"Europe/Istanbul"}')
    extra = {"google": {"thought_signature": sig}} if sig else None
    if as_extra_model:  # older SDK: unknown fields land in model_extra
        return SimpleNamespace(index=None, id="fc_1", function=fn,
                               model_extra={"extra_content": extra})
    return SimpleNamespace(index=None, id="fc_1", function=fn, extra_content=extra)


def test_thought_signature_extraction():
    assert _thought_signature(_signed_call("SIG==")) == "SIG=="
    assert _thought_signature(_signed_call("SIG==", as_extra_model=True)) == "SIG=="
    # Unsigned calls are normal — only the first of a parallel set is signed.
    assert _thought_signature(_signed_call(None)) is None
    assert _thought_signature(SimpleNamespace(function=None)) is None


def test_signature_survives_the_block_round_trip():
    signed = ToolUseBlock(id="fc_1", name="get_time", input={"tz": "UTC"}, signature="SIG==")
    plain = ToolUseBlock(id="fc_2", name="get_weather", input={"city": "Ankara"})
    dumped = blocks_to_dicts([signed, plain])
    assert dumped[0]["_signature"] == "SIG=="
    assert "_signature" not in dumped[1]  # nothing invented for unsigned calls

    out = _translate_message({"role": "assistant", "content": dumped})
    calls = out[0]["tool_calls"]
    assert calls[0]["extra_content"] == {"google": {"thought_signature": "SIG=="}}
    assert "extra_content" not in calls[1]
    # The marker itself must never reach the wire.
    assert "_signature" not in calls[0] and "_signature" not in calls[0]["function"]


def test_internal_markers_stripped_before_anthropic():
    # A turn that starts on Gemini and falls back to Anthropic mid-loop would
    # otherwise hand Anthropic a content block with an unknown key.
    msgs = [{"role": "assistant", "content": [
        {"type": "tool_use", "id": "fc_1", "name": "get_time", "input": {},
         "_signature": "SIG=="}]}]
    out = _apply_prompt_caching({"model": "claude-sonnet-4-6", "messages": msgs})
    block = out["messages"][0]["content"][0]
    assert "_signature" not in block
    assert block["name"] == "get_time"          # everything real is preserved
    assert msgs[0]["content"][0]["_signature"]  # caller's list not mutated


def _chunk(*, content=None, tool_calls=None, finish=None, usage=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=delta, finish_reason=finish)], usage=usage
    )


def _tc(*, index=None, id=None, name=None, arguments=None):
    fn = SimpleNamespace(name=name, arguments=arguments)
    return SimpleNamespace(index=index, id=id, function=fn)


async def _drain(stream):
    async for _ in stream.text_stream:
        pass
    return await stream.get_final_message()


async def test_gemini_stream_tool_call_survives(monkeypatch):
    # Full Gemini shape: preamble text, a tool call with NO id and NO index,
    # arguments split across deltas, finish_reason "stop".
    chunks = [
        _chunk(content="Let me check."),
        _chunk(tool_calls=[_tc(name="get_time", arguments='{"tz":')]),
        _chunk(tool_calls=[_tc(arguments='"UTC"}')]),
        _chunk(finish="stop"),
    ]

    class _Raw:
        async def __aiter__(self):
            for c in chunks:
                yield c

        async def close(self):
            pass

    stream = _OpenAICompatStream(None, {"model": "gemini-2.5-pro"}, "gemini")
    stream._raw = _Raw()
    msg = await _drain(stream)

    assert msg.stop_reason == "tool_use"          # not end_turn — the loop continues
    text = [b for b in msg.content if isinstance(b, TextBlock)]
    tools = [b for b in msg.content if isinstance(b, ToolUseBlock)]
    assert text[0].text == "Let me check."        # preamble survives the tool call
    assert len(tools) == 1
    assert tools[0].name == "get_time"
    assert tools[0].input == {"tz": "UTC"}        # split arguments reassembled
    assert tools[0].id                            # id synthesized when omitted


async def test_gemini_stream_parallel_calls_without_index():
    # Two calls, neither carrying an index. They must not collapse into one
    # slot (which concatenates both argument blobs into unparseable JSON).
    chunks = [
        _chunk(tool_calls=[_tc(name="search", arguments='{"q":"a"}')]),
        _chunk(tool_calls=[_tc(name="search", arguments='{"q":"b"}')]),
        _chunk(finish="tool_calls"),
    ]

    class _Raw:
        async def __aiter__(self):
            for c in chunks:
                yield c

        async def close(self):
            pass

    stream = _OpenAICompatStream(None, {}, "gemini")
    stream._raw = _Raw()
    msg = await _drain(stream)

    tools = [b for b in msg.content if isinstance(b, ToolUseBlock)]
    assert [t.input for t in tools] == [{"q": "a"}, {"q": "b"}]
    assert tools[0].id != tools[1].id  # distinct ids to pair results against


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
    r.register_legion()
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





# ── Progressive tool disclosure (defer_loading + tool_search) ────────────────
#
# The point of the mechanism is cost, so these assert the two properties that
# actually produce the saving: a deferred tool costs a NAME in the prefix
# instead of a full Rule 11 schema, and resolving one APPENDS to the tool array
# rather than rebuilding it (a rebuild would reorder the bytes the provider
# cached and invalidate the whole prefix, costing more than it saved).


class _DeferredSkill(Skill):
    name = "obscure_thing"
    deferred = True
    search_keywords = "widget frobnicate gadget"
    description = "Deferred. Does an obscure thing. Test only. Returns text."
    input_schema = {"type": "object", "properties": {}}

    async def execute(self, args, context):  # pragma: no cover
        return "ok"


@pytest.fixture
async def deferring_registry():
    r = CapabilityRegistry()
    await r.register_skill(_OfflineSkill())
    await r.register_skill(_DeferredSkill())
    return r


async def test_deferred_tool_is_absent_until_searched(deferring_registry):
    names = {t["name"] for t in deferring_registry.list_tools()}
    assert "local_thing" in names
    assert "obscure_thing" not in names   # costs a name, not a schema

    index = deferring_registry.tool_index()
    assert "obscure_thing" in index
    assert "Does an obscure thing" not in index  # the description stayed home

    loaded = {"obscure_thing"}
    assert "obscure_thing" in {
        t["name"] for t in deferring_registry.list_tools(loaded_tools=loaded)
    }


async def test_resolved_tools_append_and_never_reorder_the_prefix(deferring_registry):
    """The cache-preserving property, asserted directly: the pre-resolution tool
    array must remain a strict PREFIX of the post-resolution one."""
    before = [t["name"] for t in deferring_registry.list_tools()]
    after = [
        t["name"]
        for t in deferring_registry.list_tools(loaded_tools={"obscure_thing"})
    ]
    assert after[: len(before)] == before
    assert after[len(before):] == ["obscure_thing"]


async def test_tool_index_text_is_stable_regardless_of_what_loaded(deferring_registry):
    """tool_index() feeds the `_cache`-flagged system-prompt block, so it must
    not change shape as tools get resolved mid-session — a provider without
    Anthropic's server-side deferred resolution (Gemini and every other
    OpenAI-compat path) calls the local tool_search skill to load a deferred
    tool, and if that shrank this text it would rewrite the supposedly-cached
    prefix and kill every implicit cache hit for the rest of the session."""
    assert deferring_registry.tool_index() == deferring_registry.tool_index()
    # tool_index() takes no active_servers/loaded_tools — a caller passing the
    # session's growing sets (the old, broken call shape) must be a TypeError,
    # not a silently-accepted footgun.
    with pytest.raises(TypeError):
        deferring_registry.tool_index(loaded_tools={"obscure_thing"})


async def test_anthropic_path_flags_instead_of_withholding(deferring_registry):
    """On Anthropic the tool ships flagged so the API's own tool-search can
    resolve it; the flag is what keeps it out of the model's context."""
    by_name = {t["name"]: t for t in deferring_registry.list_tools(defer_loading=True)}
    assert by_name["obscure_thing"]["defer_loading"] is True
    assert "defer_loading" not in by_name["local_thing"]


async def test_search_matches_author_keywords_not_just_the_description(
    deferring_registry,
):
    # "frobnicate" appears nowhere in the name or description — only in the
    # keywords, which is the bridge between the owner's words and the domain's.
    assert [t["name"] for t in deferring_registry.search_tools("frobnicate")] == [
        "obscure_thing"
    ]
    assert deferring_registry.search_tools("something entirely unrelated") == []


async def test_search_never_leaks_the_keyword_marker_to_a_provider(
    deferring_registry,
):
    for tool in deferring_registry.list_tools(loaded_tools={"obscure_thing"}):
        assert not any(k.startswith("_") for k in tool)


# ── Per-turn tool memo ───────────────────────────────────────────────────────


async def test_identical_read_only_calls_run_once_per_turn():
    calls = []

    class _Counting(Skill):
        name = "counting_thing"
        read_only = True
        description = "Read-only. Counts its calls. Test only. Returns text."
        input_schema = {"type": "object", "properties": {"q": {"type": "string"}}}

        async def execute(self, args, context):
            calls.append(args)
            return f"result-{len(calls)}"

    class _Writer(_Counting):
        name = "writing_thing"
        read_only = False

    r = CapabilityRegistry()
    await r.register_skill(_Counting())
    await r.register_skill(_Writer())
    ctx = SimpleNamespace(extra={}, request_id="t", agent_id="speda")

    assert await r.execute("counting_thing", {"q": "a"}, ctx) == "result-1"
    assert await r.execute("counting_thing", {"q": "a"}, ctx) == "result-1"  # memo
    assert len(calls) == 1
    await r.execute("counting_thing", {"q": "b"}, ctx)  # different args → runs
    assert len(calls) == 2

    # A write invalidates the memo: a cached read must not outlive a mutation.
    await r.execute("writing_thing", {"q": "x"}, ctx)
    await r.execute("counting_thing", {"q": "a"}, ctx)
    assert len(calls) == 4


async def test_a_mixed_skill_memoizes_only_its_declared_read_only_command():
    """MemorySkill's actual shape: read_only=False (it can write), but `view`
    never mutates. The coarse skill-level flag alone would either block `view`
    from ever memoizing or risk memoizing a write and silently skipping a
    second, legitimately intended one — `memoizable_commands` is checked
    against THIS call's `command` arg instead."""
    calls = []

    class _Mixed(Skill):
        name = "mixed_thing"
        read_only = False
        memoizable_commands = frozenset({"view"})
        description = "Mixed read/write. Test only. Returns text."
        input_schema = {"type": "object", "properties": {
            "command": {"type": "string"}, "q": {"type": "string"},
        }}

        async def execute(self, args, context):
            calls.append(dict(args))
            return f"result-{len(calls)}"

    class _Counting(Skill):
        name = "counting_thing2"
        read_only = True
        description = "Read-only. Test only. Returns text."
        input_schema = {"type": "object", "properties": {"q": {"type": "string"}}}

        async def execute(self, args, context):
            calls.append(dict(args))
            return f"other-{len(calls)}"

    r = CapabilityRegistry()
    await r.register_skill(_Mixed())
    await r.register_skill(_Counting())
    ctx = SimpleNamespace(extra={}, request_id="t", agent_id="speda")

    # Two identical `view` calls: the second is served from the memo.
    assert await r.execute("mixed_thing", {"command": "view", "q": "a"}, ctx) == "result-1"
    assert await r.execute("mixed_thing", {"command": "view", "q": "a"}, ctx) == "result-1"
    assert len(calls) == 1

    # A `view` in between does not evict an unrelated skill's cached read —
    # the shared memo is only wiped by a call this registry could not prove
    # was safe, and `view` just was.
    await r.execute("counting_thing2", {"q": "z"}, ctx)
    before = len(calls)
    await r.execute("mixed_thing", {"command": "view", "q": "a"}, ctx)  # memo hit
    await r.execute("counting_thing2", {"q": "z"}, ctx)  # still memoized
    assert len(calls) == before

    # Two identical `create` calls: NOT memoized — a repeat write must always
    # actually run (e.g. to get its real "already exists" error back). And it
    # DOES invalidate the memo, unlike `view` above.
    calls_before_create = len(calls)
    await r.execute("mixed_thing", {"command": "create", "q": "b"}, ctx)
    await r.execute("mixed_thing", {"command": "create", "q": "b"}, ctx)
    assert len(calls) == calls_before_create + 2
    await r.execute("counting_thing2", {"q": "z"}, ctx)  # memo was wiped → runs again
    assert len(calls) == calls_before_create + 3


# ── tool_calls audit persistence ─────────────────────────────────────────────


async def test_tool_call_round_trips_through_a_real_db():
    """The core risk in wiring tool_calls up at all: does a dict `tool_input`
    actually survive SQLAlchemy's JSON column round-trip, and does a result
    that reads as an error land in both `tool_result` and `error`."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.database import Base
    from app.models.tool_call import ToolCall

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with maker() as db:
        db.add(ToolCall(
            session_id=1, request_id="req-1", tool_name="memory",
            tool_input={"command": "view", "path": "/memories/wellness/profile.md"},
            tool_result="Error: the path does not exist.",
            duration_ms=42,
            error="Error: the path does not exist.",
        ))
        await db.commit()

    async with maker() as db:
        row = (await db.execute(select(ToolCall))).scalar_one()
        assert row.tool_input == {"command": "view", "path": "/memories/wellness/profile.md"}
        assert row.duration_ms == 42
        assert row.error == row.tool_result

    await engine.dispose()


# ── Reasoning must not become dialogue ──────────────────────────────────────
# GLM, Ollama and generic OpenAI-compat gateways inline the model's thinking
# into `content` wrapped in <think>…</think>. Persisted, it becomes history —
# and the next turn reads the model's own scratchpad back as something it said.

from app.services.llm_client import _ReasoningFilter


def _filter(deltas):
    f = _ReasoningFilter()
    visible = "".join(f.feed(d) for d in deltas)
    visible += f.flush()
    return visible, "".join(f.reasoning)


def test_think_block_is_stripped_from_the_answer():
    assert _filter(["<think>which tool</think>The answer."]) == ("The answer.", "which tool")


def test_tag_split_across_chunk_boundaries_is_still_caught():
    """The whole reason this is a state machine: a stream can put "<thi" in one
    chunk and "nk>" in the next, and a per-chunk regex sees neither."""
    visible, hidden = _filter(["<thi", "nk>hid", "den</thi", "nk>Vis", "ible"])
    assert visible == "Visible"
    assert hidden == "hidden"


def test_text_without_tags_passes_through_untouched():
    assert _filter(["Just an answer."]) == ("Just an answer.", "")


def test_multiple_think_blocks_are_all_removed():
    assert _filter(["a<think>b</think>c<think>d</think>e"]) == ("ace", "bd")


def test_unterminated_think_is_salvaged_rather_than_delivering_nothing():
    """A model cut off mid-thought produced no answer. Releasing the fragment
    is visibly wrong and diagnosable; an empty push is neither."""
    visible, _ = _filter(["<think>cut off mid-th"])
    assert visible == "cut off mid-th"


def test_unterminated_think_after_real_text_keeps_only_the_text():
    visible, hidden = _filter(["The answer.<think>now let me also"])
    assert visible == "The answer."
    assert "now let me also" in hidden
