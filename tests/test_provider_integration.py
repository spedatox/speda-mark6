import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace

from fastapi import FastAPI
import httpx
import pytest
from sqlalchemy import select

from app.config import Settings
from app.core.orchestrator import AgentOrchestrator
from app.core.registry import CapabilityRegistry
from app.core.session_manager import SessionManager
from app.models.message import Message
from app.models.model_policy import ToolExecutionReceipt
from app.profiles.speda import SPEDAProfile
from app.schemas.chat import ChatRequest
from app.schemas.llm import Block, PolicyDocument, ProviderError, ThinkingRequest
from app.schemas.sse import SSEEvent, SSEEventType
from app.services.chat_turns import ChatTurns
from app.services.llm_client import LLMClient
from app.services.model_policy import ModelPolicy
from app.services.task_runner import TaskRunner
from app.skills.base import Skill
from tests.test_chat_integration import NoCapture
from tests.test_llm_streams import chat_events


class Lookup(Skill):
    name, description, read_only = "lookup", "Read the selected owner record.", True
    input_schema = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"], "additionalProperties": False}
    def __init__(self):
        self.calls = 0
    async def execute(self, args, context):
        self.calls += 1
        return "Owner's sister"


async def setup(memory, handler):
    config = Settings(_env_file=None, kimi_api_key="fixture-only")
    profile = SPEDAProfile()
    policy = ModelPolicy(memory.sessions, profile, config)
    await policy.save(1, 0, PolicyDocument(main_model="kimi:kimi-k2.6",
        models={"kimi:kimi-k2.6": ThinkingRequest(mode="enabled")}))
    client = LLMClient(policy, config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    registry = CapabilityRegistry(memory.sessions)
    lookup = Lookup()
    await registry.register_skill(lookup)
    orchestrator = AgentOrchestrator(registry, client, profile, model_policy=policy)
    turns = ChatTurns(SessionManager(), profile, orchestrator, NoCapture(), memory.sessions)
    return SimpleNamespace(policy=policy, client=client, registry=registry, lookup=lookup, orchestrator=orchestrator, turns=turns)


async def test_kimi_actual_answer_loop_tool_receipts_and_reload(memory):
    seen = []
    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        if len(seen) == 1:
            assert body["thinking"] == {"type": "enabled"}
            text = chat_events(tool=True)
        else:
            assistant = next(m for m in body["messages"] if m["role"] == "assistant")
            assert assistant["reasoning_content"] == "provider reasoning"
            text = chat_events(reasoning=False)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=text)
    bits = await setup(memory, handler)
    async with memory.sessions() as db:
        context = await bits.turns.start(db, ChatRequest(message="Who is she?"))
        output = [json.loads(value[6:]) async for value in bits.turns.stream(context)]
    assert output[-1]["type"] == "done" and not any(x["type"] == "thinking" for x in output)
    assert bits.lookup.calls == 1
    assert [x["data"]["model"] for x in output if x["type"] == "policy"] == ["kimi:kimi-k2.6"] * 2
    async with memory.sessions() as db:
        history = await SessionManager().load_history(db, context.session_id)
        receipts = (await db.execute(select(ToolExecutionReceipt))).scalars().all()
    assert len(receipts) == 1 and receipts[0].status == "completed"
    state = next(b for b in history[1]["content"] if b["type"] == "_provider_state")
    assert state["receipt"]["applied"] == {"thinking": {"type": "enabled"}}
    async with memory.sessions() as db:
        next_context = await bits.turns.start(db, ChatRequest(message="Continue.", session_id=context.session_id))
        _ = [value async for value in bits.turns.stream(next_context)]
    assert len(seen) == 3
    await bits.client.close()


async def test_failed_followup_keeps_executed_action_evidence(memory):
    count = 0
    def handler(request):
        nonlocal count
        count += 1
        text = chat_events(tool=True) if count == 1 else 'data: {"error":{"message":"failed"}}\n\n'
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=text)
    bits = await setup(memory, handler)
    async with memory.sessions() as db:
        context = await bits.turns.start(db, ChatRequest(message="Read it."))
        output = [json.loads(value[6:]) async for value in bits.turns.stream(context)]
    assert output[-1]["type"] == "error" and not any(v["type"] == "done" for v in output)
    async with memory.sessions() as db:
        history = await SessionManager().load_history(db, context.session_id)
    assert [m["role"] for m in history] == ["user", "assistant", "user"]
    assert history[-1]["content"][0]["type"] == "tool_result"
    assert bits.lookup.calls == 1
    await bits.client.close()


async def test_tool_gate_schema_dedup_and_unknown_outcome(memory):
    bits = await setup(memory, lambda _: httpx.Response(500))
    context = memory.context()
    context.effective_policy = await bits.policy.resolve()
    call = Block(type="tool_use", id="test_1", name="lookup", input={"query": "sister"})
    assert await bits.registry.execute_recorded(call, context) == "Owner's sister"
    assert await bits.registry.execute_recorded(call, context) == "Owner's sister"
    assert bits.lookup.calls == 1
    with pytest.raises(ProviderError, match="schema"):
        await bits.registry.execute_recorded(Block(type="tool_use", id="bad", name="lookup", input={}), context)
    async with memory.sessions() as db, db.begin():
        db.add(ToolExecutionReceipt(user_id=1, request_id=context.request_id, call_id="unknown",
            name="lookup", arguments_hash=__import__('hashlib').sha256(json.dumps(call.input, sort_keys=True).encode()).hexdigest(), status="started"))
    with pytest.raises(ProviderError, match="unknown outcome"):
        await bits.registry.execute_recorded(Block(type="tool_use", id="unknown", name="lookup", input=call.input), context)
    assert bits.lookup.calls == 1
    await bits.client.close()


async def test_worker_inherits_effective_policy_and_cannot_spawn_or_write(memory):
    bits = await setup(memory, lambda _: httpx.Response(500))
    parent = memory.context()
    parent.model = "kimi:kimi-k3"
    parent.effective_policy = await bits.policy.resolve(model=parent.model, request=ThinkingRequest(mode="effort", value="high"))
    captured = []
    class Engine:
        async def run(self, child):
            captured.append(child)
            yield SSEEvent(SSEEventType.CHUNK, "Result", child.session_id, child.request_id)
            yield SSEEvent(SSEEventType.DONE, {}, child.session_id, child.request_id)
    result = await TaskRunner(Engine(), bits.policy)({"prompt": "Read evidence.", "description": "Read"}, parent)
    child = captured[0]
    assert result == "Result" and child.effective_policy.wire == parent.effective_policy.wire
    assert child.request_id != parent.request_id and child.effective_policy.deadline == parent.effective_policy.deadline
    bits.registry.register_task_tool()
    class Write(Lookup):
        name, read_only = "write", False
    await bits.registry.register_skill(Write())
    assert {t["name"] for t in bits.registry.list_tools(child)} == {"lookup"}
    with pytest.raises(ProviderError, match="cannot start"):
        await TaskRunner(Engine(), bits.policy)({"prompt": "Again"}, child)
    await bits.client.close()


async def test_policy_http_acknowledgement_conflict_and_auth(memory):
    from app.database import get_db
    from app.middleware.auth import APIKeyMiddleware
    from app.routers.models import router
    bits = await setup(memory, lambda _: httpx.Response(500))
    app = FastAPI()
    app.state.model_policy = bits.policy
    app.add_middleware(APIKeyMiddleware)
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/models")).status_code == 401
        headers = {"X-API-Key": "test-only-key"}
        saved = await client.get("/model-policy", headers=headers)
        body = {"expected_revision": saved.json()["revision"], "policy": saved.json()["policy"]}
        body["policy"]["show_thinking"] = True
        response = await client.put("/model-policy", headers=headers, json=body)
        assert response.status_code == 200 and response.json()["revision"] == 2
        assert (await client.put("/model-policy", headers=headers, json=body)).status_code == 409
        catalog = (await client.get("/model-policy/catalog", headers=headers)).json()
        assert next(s for s in catalog["models"] if s["ref"] == "kimi:kimi-k3")["efforts"] == ["low", "high", "max"]
    await bits.client.close()


async def test_truncated_responses_call_is_not_replayed_or_executed(memory):
    from tests.test_llm_streams import events
    calls = []
    def handler(request):
        calls.append(request)
        raw = {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
            "output": [{"type": "function_call", "call_id": "partial", "name": "lookup", "arguments": '{"query":'}]}
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
            text=events({"type": "response.incomplete", "response": raw}))
    bits = await setup(memory, handler)
    bits.client.settings.openai_api_key = "fixture-only"
    async with memory.sessions() as db:
        context = await bits.turns.start(db, ChatRequest(message="Read it", model="openai:gpt-6-sol"))
        output = [json.loads(value[6:]) async for value in bits.turns.stream(context)]
    assert output[-1]["type"] == "error"
    assert len(calls) == 1 and bits.lookup.calls == 0
    await bits.client.close()


async def test_invalid_batch_has_no_partial_side_effects(memory):
    from tests.test_llm_streams import events
    def handler(request):
        delta = {"tool_calls": [
            {"index": 0, "id": "good", "function": {"name": "lookup", "arguments": '{"query":"sister"}'}},
            {"index": 1, "id": "bad", "function": {"name": "lookup", "arguments": '{}'}},
        ]}
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=events(
            {"choices": [{"delta": delta}]}, {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}, "[DONE]"))
    bits = await setup(memory, handler)
    async with memory.sessions() as db:
        context = await bits.turns.start(db, ChatRequest(message="Read it"))
        output = [json.loads(value[6:]) async for value in bits.turns.stream(context)]
    assert output[-1]["type"] == "error" and bits.lookup.calls == 0
    await bits.client.close()


async def test_later_tool_failure_retains_earlier_completed_result(memory, monkeypatch):
    from tests.test_llm_streams import events
    def handler(request):
        delta = {"tool_calls": [{"index": index, "id": f"call_{index}", "function": {
            "name": "lookup", "arguments": '{"query":"sister"}'}} for index in range(3)]}
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=events(
            {"choices": [{"delta": delta}]}, {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}, "[DONE]"))
    bits = await setup(memory, handler)
    original = bits.registry.execute_recorded
    async def execute(call, context):
        if call.id == "call_1":
            raise ProviderError("tool_outcome_unknown", "Unknown result")
        return await original(call, context)
    monkeypatch.setattr(bits.registry, "execute_recorded", execute)
    async with memory.sessions() as db:
        context = await bits.turns.start(db, ChatRequest(message="Read it"))
        output = [json.loads(value[6:]) async for value in bits.turns.stream(context)]
    assert output[-1]["type"] == "error" and bits.lookup.calls == 1
    async with memory.sessions() as db:
        history = await SessionManager().load_history(db, context.session_id)
    results = history[-1]["content"]
    assert results[0]["content"] == "Owner's sister"
    assert "unknown outcome" in results[1]["content"]
    assert "Not executed" in results[2]["content"]
    await bits.client.close()
