import json
from types import SimpleNamespace

import anthropic
import httpx

from app.config import Settings
from app.services.llm_client import LLMClient
from tests.test_model_policy import service
from tests.test_llm_streams import events


async def test_native_anthropic_sdk_stream_and_cache_prefix(memory):
    seen = []
    raw = [
        {"type": "message_start", "message": {"id": "msg_test", "type": "message", "role": "assistant",
            "content": [], "model": "claude-sonnet-4-6", "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 0}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "Summary"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "signed"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Answer"}},
        {"type": "content_block_stop", "index": 1},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 10}},
        {"type": "message_stop"},
    ]
    def handler(request):
        seen.append(json.loads(request.content))
        wire = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in raw)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=wire)
    config = Settings(_env_file=None, anthropic_api_key="fixture-only")
    client = LLMClient(service(memory), config)
    sdk = anthropic.AsyncAnthropic(api_key="fixture-only", max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    from app.services.anthropic_client import _cache_prefix
    client._anthropic = SimpleNamespace(client=sdk, stream_message=lambda **kw: sdk.messages.stream(**_cache_prefix(kw)))
    async with client.stream_message(model="claude-sonnet-4-6", system="identity", messages=[{"role": "user", "content": "Hello"}]) as stream:
        output = [delta async for delta in stream.event_stream()]
        message = await stream.get_final_message()
    assert output == [("thinking", "Summary"), ("text", "Answer")]
    assert message.stop_reason == "end_turn"
    assert message.continuation["payload"][0]["signature"] == "signed"
    assert seen[0]["system"][0]["cache_control"] == {"type": "ephemeral"}
    await client.close()


async def test_google_native_stream_keeps_signed_function_parts(memory):
    raw = [
        {"candidates": [{"content": {"parts": [{"thought": True, "text": "Summary", "thoughtSignature": "sig_1"}]}}]},
        {"candidates": [{"finishReason": "STOP", "content": {"parts": [
            {"functionCall": {"name": "lookup", "args": {"query": "sister"}}, "thoughtSignature": "sig_2"}]}}]},
    ]
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=events(*raw))
    config = Settings(_env_file=None, gemini_api_key="fixture-only")
    client = LLMClient(service(memory), config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    async with client.stream_message(model="gemini:gemini-3.8-flash", system="identity", messages=[]) as stream:
        first = await anext(stream.event_stream())
        message = await stream.get_final_message()  # Same iterator retains earlier parts.
    assert first == ("thinking", "Summary")
    assert message.stop_reason == "tool_use"
    assert [p["thoughtSignature"] for p in message.continuation["payload"]] == ["sig_1", "sig_2"]
    assert str(seen[0].url).endswith(":streamGenerateContent?alt=sse")
    await client.close()


def test_legacy_migration_preserves_routes_and_holds_unsupported_values():
    from scripts.import_model_policy import migrate_legacy
    document, dispositions = migrate_legacy({"agent_models": {"speda": "kimi:kimi-k3"},
        "model_thinking": {"kimi:kimi-k3": "high", "unknown-model": "high", "kimi:kimi-k2.6": "none"},
        "api_key": "must-not-be-copied"})
    assert document.agent_models == {"speda": "kimi:kimi-k3"}
    assert document.models["kimi:kimi-k3"].value == "high"
    assert document.models["kimi:kimi-k2.6"].mode == "off"
    assert next(d for d in dispositions if d.get("model") == "unknown-model")["outcome"] == "requires_review"
    assert "must-not-be-copied" not in document.model_dump_json()
