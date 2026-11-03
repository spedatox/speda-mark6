import asyncio
from dataclasses import replace
import json

import httpx
import pytest

from app.config import Settings
from app.schemas.llm import PolicyDocument, ProviderError
from app.services.llm_client import LLMClient
from tests.test_model_policy import service


def events(*values):
    return "".join("data: " + (v if isinstance(v, str) else json.dumps(v)) + "\n\n" for v in values)


def chat_events(*, reasoning=True, tool=False, broken=False, finish=True):
    values = []
    if reasoning:
        values.append({"choices": [{"delta": {"reasoning_content": "provider reasoning"}}]})
    if tool:
        values.append({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "function": {
            "name": "lookup", "arguments": '{"query":' if broken else '{"query":"sister"}'}}]}}]})
    else:
        values.append({"choices": [{"delta": {"content": "Answer"}}]})
    if finish:
        values.append({"choices": [{"delta": {}, "finish_reason": "tool_calls" if tool else "stop"}]})
        values.append("[DONE]")
    return events(*values)


def client_for(memory, handler):
    config = Settings(_env_file=None, kimi_api_key="fixture-only", openai_api_key="fixture-only", deepseek_api_key="fixture-only")
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return LLMClient(service(memory), config, http_client=http)


async def test_kimi_stream_separates_answer_and_preserves_thinking(memory):
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=chat_events(tool=True))
    client = client_for(memory, handler)
    async with client.stream_message(model="kimi:kimi-k2.6", system="identity", messages=[]) as stream:
        deltas = [event async for event in stream.event_stream()]
        message = await stream.get_final_message()
    assert deltas == [("thinking", "provider reasoning")]
    assert message.stop_reason == "tool_use"
    assert message.continuation["payload"]["reasoning_content"] == "provider reasoning"
    assert str(seen[0].url) == "https://api.moonshot.ai/v1/chat/completions"
    assert "temperature" not in json.loads(seen[0].content)
    await client.close()


@pytest.mark.parametrize("body,expected", [(chat_events(tool=True, finish=False), "interrupted"),
    (chat_events(tool=True, broken=True), "invalid_arguments")])
async def test_incomplete_stream_never_dispatches_a_tool(memory, body, expected):
    client = client_for(memory, lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"}, text=body))
    async with client.stream_message(model="kimi:kimi-k2.6", system="", messages=[]) as stream:
        try:
            message = await stream.get_final_message()
        except ProviderError as exc:
            assert exc.code == expected
        else:
            assert message.stop_reason == expected and not any(b.type == "tool_use" for b in message.content)
    await client.close()


@pytest.mark.parametrize("terminal", [None, "failed", "incomplete"])
async def test_responses_requires_terminal_success(memory, terminal):
    raw = [{"type": "response.output_text.delta", "delta": "Partial"}]
    if terminal:
        raw.append({"type": f"response.{terminal}", "response": {"status": terminal, "output": [], "incomplete_details": {"reason": "content_filter"}}})
    client = client_for(memory, lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"}, text=events(*raw)))
    async with client.stream_message(model="openai:gpt-6-sol", system="", messages=[]) as stream:
        if terminal is None:
            with pytest.raises(ProviderError, match="terminal"):
                await stream.get_final_message()
        else:
            assert (await stream.get_final_message()).stop_reason == terminal
    await client.close()


async def test_fallback_is_explicit_bounded_and_reports_actual_route(memory):
    policy = service(memory)
    await policy.save(1, 0, PolicyDocument(fallbacks=["deepseek:deepseek-v4-flash"]))
    calls = []
    def handler(request):
        calls.append(str(request.url))
        if "moonshot" in str(request.url):
            return httpx.Response(503)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=chat_events())
    client = client_for(memory, handler)
    async with client.stream_message(model="kimi:kimi-k2.6", system="", messages=[]) as stream:
        message = await stream.get_final_message()
    assert message.policy.spec.ref == "deepseek:deepseek-v4-flash" and message.attempts == 2
    assert len(calls) == 2
    await client.close()


async def test_400_and_failure_after_open_do_not_fallback(memory):
    policy = service(memory)
    await policy.save(1, 0, PolicyDocument(fallbacks=["deepseek:deepseek-v4-flash"]))
    for status, body in [(400, ""), (200, events({"error": {"message": "failed"}}))]:
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(status, headers={"content-type": "text/event-stream"}, text=body)
        client = client_for(memory, handler)
        with pytest.raises(ProviderError):
            async with client.stream_message(model="kimi:kimi-k2.6", system="", messages=[]) as stream:
                await stream.get_final_message()
        assert len(calls) == 1
        await client.close()


class SlowStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        await asyncio.sleep(1)
        yield b"data: [DONE]\n\n"


async def test_one_deadline_covers_reading_the_stream(memory):
    client = client_for(memory, lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=SlowStream()))
    policy = await client.policy.resolve(model="kimi:kimi-k2.6")
    policy = replace(policy, deadline=__import__('time').monotonic()+0.02)
    with pytest.raises(TimeoutError):
        async with client.stream_message(model=policy.spec.ref, system="", messages=[], policy=policy) as stream:
            await stream.get_final_message()
    await client.close()


async def test_fallback_recomputes_model_default_but_keeps_explicit_turn_intent(memory):
    from app.schemas.llm import ThinkingRequest
    policy = service(memory)
    await policy.save(1, 0, PolicyDocument(fallbacks=["deepseek:deepseek-v4-flash"],
        models={"kimi:kimi-k2.6": ThinkingRequest(mode="enabled"),
                "deepseek:deepseek-v4-flash": ThinkingRequest(mode="effort", value="low")}))
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(503) if "moonshot" in str(request.url) else httpx.Response(200,
            headers={"content-type": "text/event-stream"}, text=chat_events())
    client = client_for(memory, handler)
    async with client.stream_message(model="kimi:kimi-k2.6", system="", messages=[]) as stream:
        message = await stream.get_final_message()
    assert seen[-1]["reasoning_effort"] == "low"
    assert message.policy.source == "model"
    await client.close()
