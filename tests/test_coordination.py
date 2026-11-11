import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
import time
import pytest
from sqlalchemy import select, update
from app.config import Settings
from app.core.orchestrator import AgentOrchestrator
from app.core.registry import CapabilityRegistry
from app.models.coordination import CoordinationRun, AgentStep, ModelCallReceipt, Room
from app.profiles.speda import SPEDAProfile
from app.profiles.registry import ProfileRegistry
from app.schemas.coordination import CreateRoom, RoomInput, SpendingPolicy, Membership
from app.schemas.llm import LLMMessage, Block, PolicyDocument, ThinkingRequest, ProviderError
from app.services.coordination import Coordinator
from app.services.coordination_budget import CoordinationBudget, CoordinationError, ReplayStream
from app.services.model_policy import ModelPolicy
from app.services.task_runner import TaskRunner
from app.skills.collaboration import CollaborationSkill


def reply(text):
    return LLMMessage([Block(type="text", text=text)], "end_turn", {"input_tokens": 10, "output_tokens": 10})


def tool(*calls):
    return LLMMessage([Block(type="tool_use", id=f"call{i}", name="collaborate", input=c)
                       for i, c in enumerate(calls)], "tool_use")


class ScriptedClient:
    def __init__(self, script):
        self.script, self.calls = list(script), []

    @asynccontextmanager
    async def stream_message(self, **args):
        self.calls.append(args)
        result = self.script.pop(0)
        if isinstance(result, BaseException):
            raise result
        result.policy = args["policy"]
        payload = {"content": [b.model_dump() for b in result.content], "stop_reason": result.stop_reason,
                   "usage": result.usage, "continuation": result.continuation}
        yield ReplayStream(payload, result.policy)


async def setup(memory, script, *, contributions=6, calls=16, tokens=2000000):
    config = Settings(_env_file=None, chat_max_output_tokens=1024)
    base, profiles = SPEDAProfile(), ProfileRegistry()
    policy = ModelPolicy(memory.sessions, base, config)
    await policy.save(1, 0, PolicyDocument(main_model="kimi:kimi-k2.6",
        models={"kimi:kimi-k2.6": ThinkingRequest(mode="off")}))
    client = ScriptedClient(script)
    registry = CapabilityRegistry(memory.sessions)
    registry.register_task_tool()
    await registry.register_skill(CollaborationSkill())
    engine = AgentOrchestrator(registry, client, base, model_policy=policy, profiles=profiles)
    registry._task_runner = TaskRunner(engine, policy)
    budget = CoordinationBudget(memory.sessions, client)
    spending = SpendingPolicy(paused=False, daily_calls=100, daily_tokens=10000000,
        daily_microdollars=10000000, run={"calls": calls, "tokens": tokens,
        "microdollars": 2000000, "seconds": 300, "contributions": contributions},
        prices={"kimi:kimi-k2.6": {"microdollars_per_token": 1}})
    await budget.save(1, 0, spending)
    coordinator = Coordinator(memory.sessions, profiles, engine, policy, budget)
    return SimpleNamespace(coordinator=coordinator, client=client, budget=budget, policy=policy,
        engine=engine, spending=spending, profiles=profiles)


async def send(bits, room, message="Discuss the architecture", input_id="one"):
    result = await bits.coordinator.start(1, room["id"], RoomInput(input_id=input_id, message=message))
    task = bits.coordinator.live.get(result["run_id"])
    if task:
        await task
    return await bits.coordinator.inspect_run(1, result["run_id"])


async def test_real_separate_personas_challenge_revise_and_reconnect(memory):
    bits = await setup(memory, [
        tool({"action": "address", "agent_id": "optimus", "message": "Propose an architecture"},
             {"action": "address", "agent_id": "scourge", "message": "Challenge the proposed architecture"}),
        reply("Optimus, what architecture do you propose? Scourge, challenge its assumptions."), reply("Use a single shared writable checkout."),
        tool({"action": "address", "agent_id": "optimus", "message": "Two writers race. Isolate their checkouts."}),
        reply("A shared writable checkout permits conflicting writes."),
        reply("Scourge is right. Isolate worker checkouts and appoint one integration owner."),
        reply("That resolves the concurrent writer objection."),
        reply("The decision is isolated checkouts with one integration owner.")])
    room = await bits.coordinator.create(1, CreateRoom(members=["optimus", "scourge"]))
    result = await send(bits, room)
    assert result["status"] == "succeeded", await bits.coordinator.events(1, room["id"])
    assert result["contributions"] == 6
    assert len(bits.client.calls) == 8
    assert "You are Scourge" in bits.client.calls[3]["system"]
    assert "shared writable checkout permits" in str(bits.client.calls[5]["messages"])
    events = await bits.coordinator.events(1, room["id"])
    spoken = [e for e in events["events"] if e["kind"] == "message"]
    assert [e["speaker"] for e in spoken] == ["speda", "optimus", "scourge", "optimus", "scourge", "speda"]
    assert (await bits.coordinator.events(1, room["id"], events["next_cursor"]))["events"] == []
    duplicate = await bits.coordinator.start(1, room["id"], RoomInput(input_id="one", message="Discuss the architecture"))
    assert duplicate["duplicate"] and duplicate["run_id"] == result["id"]
    assert len(bits.client.calls) == 8
    await bits.coordinator.shutdown()


async def test_direct_chat_single_call_owner_scope_and_alias(memory):
    bits = await setup(memory, [reply("Friday here.")])
    room = await bits.coordinator.create(1, CreateRoom(lead="nightcrawler"))
    result = await send(bits, room, "Hello")
    assert result["status"] == "succeeded" and len(bits.client.calls) == 1
    assert "You are Friday" in bits.client.calls[0]["system"]
    assert bits.profiles.require("friday").stable_id == "nightcrawler"
    with pytest.raises(CoordinationError):
        await bits.coordinator.events(2, room["id"])
    with pytest.raises(CoordinationError):
        await bits.coordinator.start(1, room["id"], RoomInput(input_id="one", message="Different"))
    with pytest.raises(CoordinationError):
        await bits.coordinator.membership(1, room["id"], Membership(expected_version=99, action="invite", agent_id="optimus"))
    await bits.coordinator.shutdown()


async def test_parallel_memory_requests_keep_the_first_result_and_finish_the_turn(memory):
    import json

    from app.services.memory_runtime import MemoryRuntime
    from app.skills.memory_lookup import MemoryLookupSkill

    await memory.seed("Mira is the owner's sister and used to live in Izmir.", title="Mira", aliases=("Mira",))
    batch = LLMMessage([
        Block(type="tool_use", id="personal", name="memory_lookup", input={"query": "Mira"}),
        Block(type="tool_use", id="history", name="memory_lookup",
              input={"query": "Mira", "corpus": "conversation_history"}),
    ], "tool_use")
    bits = await setup(memory, [batch, reply("Mira used to live in Izmir.")])
    await bits.engine._registry.register_skill(MemoryLookupSkill(MemoryRuntime(memory.store)))
    try:
        room = await bits.coordinator.create(1, CreateRoom())
        run = await send(bits, room, "What do you remember about Mira?")
        assert run["status"] == "succeeded"
        followup = bits.client.calls[1]
        assert "memory_lookup" not in {tool["name"] for tool in followup["tools"]}
        results = [block for message in followup["messages"] if isinstance(message["content"], list)
                   for block in message["content"] if block["type"] == "tool_result"]
        assert [item["tool_use_id"] for item in results] == ["personal", "history"]
        assert "Izmir" in results[0]["content"]
        assert json.loads(results[1]["content"])["status"] == "budget_exhausted"
    finally:
        await bits.coordinator.shutdown()


async def test_mcp_browse_rejection_finishes_the_turn_without_an_unknown_receipt(memory):
    import json
    from mcp.server import MCPServer
    from app.mcp.client import MCPClient
    from app.models.model_policy import ToolExecutionReceipt
    from app.skills.mcp_data import MCPDataSkill
    from app.skills.tool_search import ToolSearchSkill

    discover = LLMMessage([Block(type="tool_use", id="discover", name="tool_search",
        input={"query": "mcp_browse"})], "tool_use")
    browse = LLMMessage([Block(type="tool_use", id="browse", name="mcp_browse",
        input={"server": "iacon", "operation": "list_resources"})], "tool_use")
    bits = await setup(memory, [discover, browse, reply("I could not reach that connection.")])
    registry = bits.engine._registry
    client = MCPClient("fixture", target=MCPServer("fixture"), allowed_agents={"speda"})
    await registry.register_mcp(client)
    await registry.register_skill(MCPDataSkill(registry))
    await registry.register_skill(ToolSearchSkill(registry))
    try:
        room = await bits.coordinator.create(1, CreateRoom())
        run = await send(bits, room, "Check Iacon")
        assert run["status"] == "succeeded"
        results = [block for message in bits.client.calls[2]["messages"] if isinstance(message["content"], list)
                   for block in message["content"] if block["type"] == "tool_result"]
        result, = [block for block in results if block["tool_use_id"] == "browse"]
        assert json.loads(result["content"])["code"] == "unauthorized_mcp"
        async with memory.sessions() as db:
            receipt = await db.scalar(select(ToolExecutionReceipt).where(ToolExecutionReceipt.call_id == "browse"))
        assert receipt.status == "completed" and client.calls == 0
    finally:
        await bits.coordinator.shutdown()
        await registry.shutdown_adapters()


async def test_unknown_paid_response_is_held_never_resent(memory):
    bits = await setup(memory, [ProviderError("disconnect", "Connection lost after dispatch"), reply("New turn")])
    await bits.budget.save(1, 1, bits.spending.model_copy(update={"concurrent_calls": 1}))
    room = await bits.coordinator.create(1, CreateRoom())
    result = await send(bits, room)
    assert result["status"] == "reconciling"
    assert result["calls"][0]["status"] == "unknown"
    with pytest.raises(CoordinationError, match="reconciliation"):
        await bits.coordinator.resume(1, result["id"])
    assert len(bits.client.calls) == 1
    separate_room = await bits.coordinator.create(1, CreateRoom())
    new_turn = await send(bits, separate_room, input_id="independent")
    assert new_turn["status"] == "succeeded"
    assert (await bits.coordinator.inspect_run(1, result["id"]))["calls"][0]["status"] == "unknown"
    assert len(bits.client.calls) == 2
    await bits.coordinator.shutdown()


@pytest.mark.parametrize(("lead", "expected_calls", "expected_status"), [
    ("speda", 2, "reconciling"),
    ("optimus", 3, "succeeded"),
    ("scourge", 3, "succeeded"),
])
async def test_shared_worker_budget_and_anonymous_prompt(memory, lead, expected_calls, expected_status):
    task = LLMMessage([Block(type="tool_use", id="worker", name="Task", input={
        "description": "Read evidence", "prompt": "Read the evidence", "subagent_type": "general-purpose"})], "tool_use")
    bits = await setup(memory, [task, reply("Evidence read."), reply("Here is the evidence.")], calls=2)
    room = await bits.coordinator.create(1, CreateRoom(lead=lead))
    result = await send(bits, room)
    assert len(bits.client.calls) == expected_calls, await bits.coordinator.events(1, room["id"])
    assert "anonymous read-only task worker" in bits.client.calls[1]["system"]
    assert not any(t["name"] in {"Task", "collaborate"} for t in bits.client.calls[1]["tools"])
    assert result["status"] == expected_status and result["reserved"]["calls"] == expected_calls
    await bits.coordinator.shutdown()


async def test_consult_reserves_return_slot(memory):
    bits = await setup(memory, [
        tool({"action": "consult", "agent_id": "scourge", "message": "Review this"}),
        reply("I can review the available evidence myself."),
    ], contributions=2)
    room = await bits.coordinator.create(1, CreateRoom())
    result = await send(bits, room)
    assert len(result["steps"]) == 1 and len(bits.client.calls) == 2
    assert result["status"] == "succeeded"
    assert "round_limit" in str(bits.client.calls[-1]["messages"])
    await bits.coordinator.shutdown()


async def test_parallel_duplicate_input_and_daily_cap(memory):
    bits = await setup(memory, [reply("Only admitted once")])
    room = await bits.coordinator.create(1, CreateRoom())
    original = bits.coordinator.launch
    bits.coordinator.launch = lambda _: None
    body = RoomInput(input_id="same", message="hello")
    results = await asyncio.gather(*(bits.coordinator.start(1, room["id"], body) for _ in range(4)))
    assert len({r["run_id"] for r in results}) == 1
    assert sum(not r["duplicate"] for r in results) == 1
    run_id = results[0]["run_id"]
    await bits.budget.save(1, 1, bits.spending.model_copy(update={"daily_calls": 0}))
    await bits.coordinator.execute(run_id)
    assert not bits.client.calls
    assert (await bits.coordinator.inspect_run(1, run_id))["reserved"]["calls"] == 0
    await bits.coordinator.shutdown()


async def test_paid_response_replays_after_delivery_crash_without_another_charge(memory):
    bits = await setup(memory, [reply("Saved answer")])
    room = await bits.coordinator.create(1, CreateRoom())
    original = bits.engine.run
    async def interrupted(context):
        async for event in original(context):
            yield event
        raise RuntimeError("Crash before step result commit")
    bits.engine.run = interrupted
    result = await send(bits, room)
    assert result["status"] == "reconciling" and result["calls"][0]["status"] == "complete"
    bits.engine.run = original
    resumed = await bits.coordinator.resume(1, result["id"])
    await bits.coordinator.live[resumed["run_id"]]
    final = await bits.coordinator.inspect_run(1, result["id"])
    assert final["status"] == "succeeded" and final["steps"][0]["result"] == "Saved answer"
    assert len(bits.client.calls) == 1 and final["reserved"]["calls"] == 1
    await bits.coordinator.shutdown()


async def test_address_to_a_nonmember_returns_a_denial_and_finishes_the_turn(memory):
    import json
    from app.models.model_policy import ToolExecutionReceipt

    bits = await setup(memory, [
        tool({"action": "address", "agent_id": "optimus", "message": "Check the assumption"}),
        reply("Optimus is not in this room."),
    ])
    try:
        room = await bits.coordinator.create(1, CreateRoom())
        result = await send(bits, room)
        assert result["status"] == "succeeded" and len(result["steps"]) == 1
        async with memory.sessions() as db:
            receipt = await db.scalar(select(ToolExecutionReceipt).where(ToolExecutionReceipt.name == "collaborate"))
            assert receipt.status == "completed"
            assert json.loads(receipt.result["text"])["code"] == "not_member"
    finally:
        await bits.coordinator.shutdown()


async def test_consult_returns_to_caller_without_changing_membership(memory):
    bits = await setup(memory, [tool({"action": "consult", "agent_id": "scourge", "message": "Check the assumption"}),
        reply("Checking it."),
        tool({"action": "address", "agent_id": "speda", "message": "Publish a private observation"}),
        reply("The assumption fails under concurrent writes."), reply("I have revised the plan.")], contributions=3)
    room = await bits.coordinator.create(1, CreateRoom())
    result = await send(bits, room)
    assert result["status"] == "succeeded", await bits.coordinator.events(1, room["id"])
    assert result["contributions"] == 3
    assert (await bits.coordinator.rooms(1))[0]["members"] == ["speda"]
    assert "private_exchange" in str(bits.client.calls[-2]["messages"])
    assert "assumption fails" in str(bits.client.calls[-1]["messages"])
    assert all(e["kind"] != "contribution_queued" or e["payload"]["mode"] == "consult"
               for e in (await bits.coordinator.events(1, room["id"]))["events"])
    await bits.coordinator.shutdown()


@pytest.mark.parametrize(("lead", "tokens"), [("speda", 2000000), ("optimus", 100)])
async def test_parallel_model_reservations_never_exceed_root(memory, lead, tokens):
    bits = await setup(memory, [], calls=1, tokens=tokens)
    room = await bits.coordinator.create(1, CreateRoom(lead=lead))
    bits.coordinator.launch = lambda _: None
    receipt = await bits.coordinator.start(1, room["id"], RoomInput(input_id="one", message="hello"))
    async with memory.sessions() as db, db.begin():
        run = await db.get(CoordinationRun, receipt["run_id"])
        run.status, run.executor, run.lease_until = "running", bits.coordinator.identity, time.time()+60
        step = await db.scalar(select(AgentStep).where(AgentStep.run_id == run.id))
    policy = await bits.policy.resolve(user_id=1)
    ctx = memory.context(run_id=run.id, step_id=step.id, coordination=bits.coordinator)
    admitted = await asyncio.gather(*(bits.budget.reserve(ctx, f"slot{i}", policy, {"input": i}, 100)
                                    for i in range(4)), return_exceptions=True)
    assert sum(not isinstance(x, BaseException) for x in admitted) == 1
    assert all(not isinstance(x, BaseException) or isinstance(x, CoordinationError) for x in admitted)
    assert (await bits.coordinator.inspect_run(1, run.id))["reserved"]["calls"] == 1


async def test_exempt_run_settles_reported_tokens_and_holds_unknown_calls(memory):
    from app.models.coordination import CoordinationDay

    first = LLMMessage([Block(type="text", text="Read the file.")], "end_turn",
                       {"prompt_tokens": 140, "completion_tokens": 20, "total_tokens": 160})
    bits = await setup(memory, [first, ProviderError("disconnect", "Lost after send")], calls=1, tokens=10000)
    room = await bits.coordinator.create(1, CreateRoom(lead="optimus"))
    bits.coordinator.launch = lambda _: None
    admitted = await bits.coordinator.start(1, room["id"], RoomInput(input_id="settle", message="Read this"))
    async with memory.sessions() as db, db.begin():
        run = await db.get(CoordinationRun, admitted["run_id"])
        run.status, run.executor, run.lease_until = "running", bits.coordinator.identity, time.time() + 60
        step = await db.scalar(select(AgentStep).where(AgentStep.run_id == run.id))

    ctx = memory.context(agent_id="optimus", room_id=room["id"], run_id=run.id, step_id=step.id,
                         coordination=bits.coordinator)
    args = {"policy": await bits.policy.resolve(user_id=1), "system": "file context " * 400,
            "messages": [], "max_tokens": 512}
    try:
        async with bits.budget.stream(ctx, **args):
            pass
        async with memory.sessions() as db:
            assert (await db.get(CoordinationRun, run.id)).tokens == 160
        with pytest.raises(ProviderError, match="Lost after send"):
            async with bits.budget.stream(ctx, **args):
                pytest.fail("The disconnected provider cannot return a stream")
        with pytest.raises(CoordinationError, match="reserved tokens") as blocked:
            async with bits.budget.stream(ctx, **args):
                pytest.fail("An unknown call must retain its full reservation")
        assert blocked.value.code == "run_budget"
        async with memory.sessions() as db:
            final = await db.get(CoordinationRun, run.id)
            receipts = list(await db.scalars(select(ModelCallReceipt).where(ModelCallReceipt.run_id == run.id)))
            day = await db.scalar(select(CoordinationDay).where(CoordinationDay.user_id == 1))
            assert final.calls == len(bits.client.calls) == 2
            assert final.tokens == 160 + next(receipt.tokens for receipt in receipts if receipt.status == "unknown")
            assert day.tokens == sum(receipt.tokens for receipt in receipts)
            assert day.microdollars == final.microdollars == sum(receipt.microdollars for receipt in receipts)
    finally:
        await bits.coordinator.shutdown()


async def test_cancellation_joins_stream_and_holds_uncertain_call(memory):
    started, closed = asyncio.Event(), asyncio.Event()
    bits = await setup(memory, [])
    class SlowStream:
        async def event_stream(self):
            started.set()
            yield "text", "Partial"
            await asyncio.Event().wait()
        async def get_final_message(self):
            raise AssertionError("Cancelled stream cannot be complete")
    @asynccontextmanager
    async def slow(**args):
        try:
            yield SlowStream()
        finally:
            closed.set()
    bits.client.stream_message = slow
    room = await bits.coordinator.create(1, CreateRoom())
    receipt = await bits.coordinator.start(1, room["id"], RoomInput(input_id="one", message="hello"))
    await asyncio.wait_for(started.wait(), 5)
    result = await bits.coordinator.cancel(1, receipt["run_id"])
    assert closed.is_set() and result["run"]["cancelled"]
    assert result["run"]["status"] == "reconciling"
    assert result["run"]["calls"][0]["status"] == "unknown"
    assert not bits.coordinator.live
    await bits.coordinator.shutdown()


async def test_queued_owner_input_waits_and_does_not_leak_into_active_round(memory):
    bits = await setup(memory, [tool({"action": "invite", "agent_id": "optimus", "message": "Review the first request"}),
        reply("Inviting Optimus"), reply("First request reviewed"), reply("Second request answered")])
    entered, release = asyncio.Event(), asyncio.Event()
    original = bits.client.stream_message
    @asynccontextmanager
    async def gated(**args):
        if not bits.client.calls:
            entered.set()
            await release.wait()
        async with original(**args) as stream:
            yield stream
    bits.client.stream_message = gated
    room = await bits.coordinator.create(1, CreateRoom())
    first = await bits.coordinator.start(1, room["id"], RoomInput(input_id="first", message="FIRST_OWNER_REQUEST"))
    first_task = bits.coordinator.live[first["run_id"]]
    await asyncio.wait_for(entered.wait(), 5)
    second = await bits.coordinator.start(1, room["id"], RoomInput(input_id="second", message="SECOND_OWNER_REQUEST"))
    assert second["status"] == "waiting_room" and second["run_id"] not in bits.coordinator.live
    with pytest.raises(CoordinationError, match="active"):
        await bits.coordinator.start(1, room["id"], RoomInput(input_id="reject", message="Reject", on_busy="reject"))
    release.set()
    await asyncio.wait_for(first_task, 10)
    while bits.coordinator.live:
        await asyncio.wait_for(asyncio.gather(*list(bits.coordinator.live.values())), 10)
    assert len(bits.client.calls) == 4
    for call in bits.client.calls[:3]:
        assert "SECOND_OWNER_REQUEST" not in str(call["messages"])
    assert "FIRST_OWNER_REQUEST" in str(bits.client.calls[2]["messages"])
    assert "Second request" not in str(bits.client.calls[2]["messages"])
    assert "SECOND_OWNER_REQUEST" in str(bits.client.calls[3]["messages"])
    assert (await bits.coordinator.inspect_run(1, second["run_id"]))["status"] == "succeeded"
    await bits.coordinator.shutdown()


async def test_stop_reply_at_delivery_boundary_does_not_publish_completed_answer(memory):
    bits = await setup(memory, [reply("Paid result")])
    entered, release = asyncio.Event(), asyncio.Event()
    original = bits.coordinator._speak
    async def delayed(*args):
        text = await original(*args)
        entered.set()
        await release.wait()
        return text
    bits.coordinator._speak = delayed
    room = await bits.coordinator.create(1, CreateRoom())
    admitted = await bits.coordinator.start(1, room["id"], RoomInput(input_id="stop", message="Respond"))
    task = bits.coordinator.live[admitted["run_id"]]
    await asyncio.wait_for(entered.wait(), 5)
    run = await bits.coordinator.inspect_run(1, admitted["run_id"])
    step_id = run["steps"][0]["id"]
    await bits.coordinator.stop_reply(1, step_id)
    release.set()
    await asyncio.wait_for(task, 5)
    run = await bits.coordinator.inspect_run(1, admitted["run_id"])
    assert run["status"] == "partial" and not run["cancelled"]
    assert run["steps"][0]["status"] == "cancelled" and run["steps"][0]["result"] is None
    assert run["calls"][0]["status"] == "complete"
    events = (await bits.coordinator.events(1, room["id"]))["events"]
    assert not any(e["kind"] == "message" for e in events)
    assert any(e["kind"] == "delta" and e["payload"]["text"] == "Paid result" for e in events)
    assert not (await bits.coordinator.stop_reply(1, step_id))["accepted"]
    await bits.coordinator.shutdown()


@pytest.mark.parametrize("scope", ["run", "reply"])
async def test_cancelled_tool_with_completed_provider_call_requires_reconciliation(memory, scope):
    bits = await setup(memory, [tool({"action": "invite", "agent_id": "optimus", "message": "Review"})])
    entered = asyncio.Event()
    async def interrupted_effect(*args):
        entered.set()
        await asyncio.Event().wait()
    bits.engine._registry.execute = interrupted_effect
    room = await bits.coordinator.create(1, CreateRoom())
    admitted = await bits.coordinator.start(1, room["id"], RoomInput(input_id="one", message="Ask Optimus"))
    task = bits.coordinator.live[admitted["run_id"]]
    await asyncio.wait_for(entered.wait(), 5)
    if scope == "run":
        await bits.coordinator.cancel(1, admitted["run_id"])
    else:
        run = await bits.coordinator.inspect_run(1, admitted["run_id"])
        await bits.coordinator.stop_reply(1, run["steps"][0]["id"])
    await asyncio.wait_for(task, 5)
    result = await bits.coordinator.inspect_run(1, admitted["run_id"])
    assert result["calls"][0]["status"] == "complete"
    assert result["status"] == "reconciling"
    from app.models.model_policy import ToolExecutionReceipt
    async with memory.sessions() as db:
        effect = await db.scalar(select(ToolExecutionReceipt).where(
            ToolExecutionReceipt.request_id == result["steps"][0]["id"]))
        assert effect.status == "started"
    await bits.coordinator.shutdown()
