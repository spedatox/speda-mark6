"""Daybreak policy stays model-specific across every Responses call path."""
import json

import httpx
import pytest

from app.config import Settings
from app.schemas.llm import PolicyDocument, ProviderError
from app.services.llm_client import LLMClient
from app.services.llm_protocol import request_body
from app.services.client_models import catalog, set_daybreak
from tests.test_model_policy import service


@pytest.mark.parametrize("model,program", [
    ("openai:gpt-6-sol", "daybreak_blue"),
    ("openai:gpt-6.1-sol", "daybreak_blue"),
    ("openai:gpt-6-astra", "daybreak_blue"),
    ("openai:gpt-5.6-cyber", "daybreak_red"),
    ("openai:gpt-6-cyber", "daybreak_red"),
])
async def test_declared_model_program_is_sent_and_pinned(memory, model, program):
    policy = service(memory)
    await policy.save(1, 0, PolicyDocument(cyber_programs={model: program}))
    first = await policy.resolve(model=model)
    assert request_body(first, "", [], [], 1024)["access_programs"] == {"cyber": program}
    replacement = "standard" if program == "daybreak_blue" else None
    await policy.save(1, 1, PolicyDocument(cyber_programs={model: replacement} if replacement else {}))
    worker = await policy.resolve_worker_policy("researcher", parent=first)
    assert worker.wire["access_programs"] == {"cyber": program}
    assert (await policy.resolve(model=model)).wire.get("access_programs") == (
        {"cyber": "standard"} if replacement else None)


async def test_unspecified_and_incompatible_programs(memory):
    policy = service(memory)
    assert "access_programs" not in request_body(await policy.resolve(model="openai:gpt-6-sol"), "", [], [], 1024)
    for model, program in [
        ("openai:gpt-6-sol", "daybreak_red"),
        ("openai:gpt-6-cyber", "standard"),
        ("openai:gpt-6-luna", "daybreak_blue"),
        ("openai:unknown-model", "daybreak_blue"),
        ("kimi:kimi-k3", "daybreak_blue"),
    ]:
        with pytest.raises(ValueError):
            await policy.save(1, 0, PolicyDocument(cyber_programs={model: program}))
    assert (await policy.snapshot())[0] == 0


async def test_picker_switch_uses_persisted_model_policy(memory):
    policy = service(memory)
    rows = {row["id"]: row for row in await catalog(policy)}
    assert rows["openai:gpt-6-sol"]["daybreak_program"] == "daybreak_blue"
    assert rows["openai:gpt-6-sol"]["daybreak_selection"] is None
    assert rows["openai:gpt-6-cyber"]["daybreak_required"]
    enabled = await set_daybreak(policy, 1, "openai:gpt-6-sol", True)
    assert enabled["daybreak_selection"] == "daybreak_blue"
    assert (await policy.resolve(model="openai:gpt-6-sol")).wire["access_programs"] == {"cyber": "daybreak_blue"}
    disabled = await set_daybreak(policy, 1, "openai:gpt-6-sol", False)
    assert disabled["daybreak_selection"] == "standard"
    assert (await policy.resolve(model="openai:gpt-6-sol")).wire["access_programs"] == {"cyber": "standard"}
    with pytest.raises(ValueError, match="requires Daybreak Red"):
        await set_daybreak(policy, 1, "openai:gpt-6-cyber", False)
    with pytest.raises(ValueError, match="no declared Daybreak support"):
        await set_daybreak(policy, 1, "openai:gpt-6-luna", True)


async def test_response_receipt_and_denied_access_are_clear(memory):
    policy = service(memory)
    await policy.save(1, 0, PolicyDocument(cyber_programs={"openai:gpt-6-sol": "daybreak_blue"}))
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        terminal = {"type": "response.completed", "response": {"status": "completed",
            "model": "gpt-6-sol", "access_programs": {"cyber": "daybreak_blue"},
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "Done"}]}]}}
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
            text="data: " + json.dumps(terminal) + "\n\n")
    client = LLMClient(policy, Settings(_env_file=None, openai_api_key="fixture"),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    async with client.stream_message(model="openai:gpt-6-sol", system="", messages=[]) as stream:
        message = await stream.get_final_message()
    assert seen[0]["access_programs"] == {"cyber": "daybreak_blue"}
    assert message.continuation["receipt"]["selected_cyber_program"] == "daybreak_blue"
    assert message.continuation["receipt"]["response_model"] == "gpt-6-sol"
    await client.close()

    denied = lambda _: httpx.Response(403, json={"error": {"code": "access_program_not_enabled", "message": "secret"}})
    client = LLMClient(policy, Settings(_env_file=None, openai_api_key="fixture"),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(denied)))
    with pytest.raises(ProviderError, match="organization and project") as error:
        async with client.stream_message(model="openai:gpt-6-sol", system="", messages=[]):
            pass
    assert error.value.code == "access_program_not_enabled"
    await client.close()


async def test_cyber_model_uses_stable_pseudonymous_safety_identifier(memory):
    policy = service(memory)
    await policy.save(42, 0, PolicyDocument(cyber_programs={"openai:gpt-6-cyber": "daybreak_red"}))
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(403, json={"error": {"code": "access_program_not_enabled"}})
    client = LLMClient(policy, Settings(_env_file=None, openai_api_key="fixture", speda_api_key="test-secret"),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    for _ in range(2):
        with pytest.raises(ProviderError):
            async with client.stream_message(model="openai:gpt-6-cyber", system="", messages=[], user_id=42):
                pass
    assert requests[0]["access_programs"] == {"cyber": "daybreak_red"}
    assert requests[0]["safety_identifier"] == requests[1]["safety_identifier"]
    assert requests[0]["safety_identifier"].startswith("user_")
    assert requests[0]["safety_identifier"] != "user_42"
    await client.close()
