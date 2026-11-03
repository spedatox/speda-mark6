import json

import pytest

from app.schemas.llm import ProviderError, ThinkingRequest
from app.services.llm_protocol import arguments, decode, request_body
from tests.test_model_policy import service


TOOL = {"name": "lookup", "description": "Read records.", "input_schema": {
    "type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}


async def test_responses_preserves_encrypted_reasoning_and_call_ids(memory):
    policy = await service(memory).resolve(model="openai:gpt-6-sol", request=ThinkingRequest(mode="effort", value="high"))
    output = [{"type": "reasoning", "id": "rs_1", "encrypted_content": "opaque", "summary": []},
              {"type": "function_call", "call_id": "call_1", "id": "fc_1", "name": "lookup", "arguments": '{"query":"sister"}', "status": "completed"}]
    message = decode(policy, {"status": "completed", "output": output})
    history = [{"role": "user", "content": "Who is she?"}, {"role": "assistant", "content": [
        *(b.model_dump() for b in message.content), message.continuation]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "Owner's sister"}]}]
    body = request_body(policy, "identity", history, [TOOL], 16000)
    assert body["input"][1:3] == output
    assert body["input"][3]["call_id"] == "call_1"
    assert body["store"] is False and body["tools"][0]["strict"] is False
    assert body["reasoning"] == {"effort": "high"}


@pytest.mark.parametrize("status,details,expected", [("failed", None, "failed"),
    ("incomplete", {"reason": "content_filter"}, "incomplete"),
    ("incomplete", {"reason": "max_output_tokens"}, "max_tokens"),
    ("in_progress", None, "interrupted"), ("cancelled", None, "cancelled")])
async def test_failure_never_looks_completed(memory, status, details, expected):
    policy = await service(memory).resolve(model="openai:gpt-6-sol")
    message = decode(policy, {"status": status, "incomplete_details": details,
        "output": [{"type": "function_call", "call_id": "partial", "name": "lookup", "arguments": '{"query":'}]})
    assert message.stop_reason == expected and not message.content


@pytest.mark.parametrize("raw", ['{"broken":', "[]", "null", None, ""])
def test_invalid_tool_arguments_never_become_empty_defaults(raw):
    with pytest.raises(ProviderError, match="arguments"):
        arguments(raw)
    assert arguments("{}") == {}


async def test_kimi_thinking_tool_roundtrip_and_cross_model_rejection(memory):
    policies = service(memory)
    policy = await policies.resolve(model="kimi:kimi-k2.6", request=ThinkingRequest(mode="enabled"))
    native = {"role": "assistant", "content": "", "reasoning_content": "provider continuation",
        "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "lookup", "arguments": '{"query":"sister"}'}}]}
    message = decode(policy, {"choices": [{"finish_reason": "tool_calls", "message": native}]})
    history = [{"role": "assistant", "content": [*(b.model_dump() for b in message.content), message.continuation]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "evidence"}]}]
    assert request_body(policy, "identity", history, [TOOL], 16000)["messages"][1] == native
    other = await policies.resolve(model="deepseek:deepseek-v4-flash")
    with pytest.raises(ProviderError, match="another model"):
        request_body(other, "identity", history, [TOOL], 16000)


async def test_google_signed_parts_are_unmodified_and_replayed(memory):
    policy = await service(memory).resolve(model="gemini:gemini-2.5-flash", request=ThinkingRequest(mode="budget", value=2048))
    parts = [{"thought": True, "text": "summary", "thoughtSignature": "opaque"},
             {"functionCall": {"name": "lookup", "args": {"query": "sister"}}, "thoughtSignature": "signature"}]
    before = json.dumps(parts)
    message = decode(policy, {"candidates": [{"finishReason": "STOP", "content": {"parts": parts}}]})
    assert json.dumps(parts) == before
    call = next(b for b in message.content if b.type == "tool_use")
    history = [{"role": "assistant", "content": [call.model_dump(), message.continuation]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call.id, "content": "evidence"}]}]
    body = request_body(policy, "identity", history, [TOOL], 16000)
    assert body["contents"][0]["parts"] == parts
    assert body["contents"][1]["parts"][0]["functionResponse"]["name"] == "lookup"
    assert body["generationConfig"]["thinkingConfig"]["thinkingBudget"] == 2048


async def test_anthropic_signed_blocks_survive_boundary(memory):
    policy = await service(memory).resolve(model="claude-sonnet-4-6")
    native = [{"type": "thinking", "thinking": "summary", "signature": "signed"}, {"type": "text", "text": "Answer"}]
    message = decode(policy, {"stop_reason": "end_turn", "content": native})
    body = request_body(policy, "identity", [{"role": "assistant", "content": [message.content[0].model_dump(), message.continuation]}], [], 16000)
    assert body["messages"][0]["content"] == native


async def test_off_and_provider_default_are_distinct_and_budget_cannot_eat_answer(memory):
    policies = service(memory)
    off = await policies.resolve(model="kimi:kimi-k2.6", request=ThinkingRequest(mode="off"))
    default = await policies.resolve(model="kimi:kimi-k2.6")
    assert request_body(off, "", [], [], 16000)["thinking"] == {"type": "disabled"}
    assert "thinking" not in request_body(default, "", [], [], 16000)
    budget = await policies.resolve(model="claude-haiku-4-5-20251001", request=ThinkingRequest(mode="budget", value=4096))
    with pytest.raises(ValueError, match="room"):
        request_body(budget, "", [], [], 3000)


async def test_summary_visibility_changes_only_display_parameter(memory):
    from dataclasses import replace
    policy = await service(memory).resolve(model="openai:gpt-6-sol", request=ThinkingRequest(mode="effort", value="low"))
    hidden = request_body(policy, "", [], [], 16000)
    visible = request_body(replace(policy, show_thinking=True), "", [], [], 16000)
    assert hidden["reasoning"] == {"effort": "low"}
    assert visible["reasoning"] == {"effort": "low", "summary": "auto"}
    assert hidden["input"] == visible["input"]
