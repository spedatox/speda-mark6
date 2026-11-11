"""Peer exchanges use real turns and return actual answers without owner relays."""
import json
from contextlib import asynccontextmanager
import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.coordination import AgentStep, Room
from app.schemas.client_compat import ClientChat
from app.schemas.coordination import CreateRoom, RoomInput
from app.services.client_compat import ClientCompatibility
from tests.test_coordination import setup, send, tool, reply
from tests.test_heartbreaker_rooms import completed, frames


def collaboration_packet(call):
    for message in reversed(call["messages"]):
        content = message["content"]
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content
        for block in reversed(blocks):
            if block.get("type") == "text" and block["text"].startswith('{"collaboration_request"'):
                return json.loads(block["text"])
    raise AssertionError("No collaboration packet in provider context")


async def session_id(memory, room):
    async with memory.sessions() as db:
        return (await db.get(Room, room["id"])).session_id


@pytest.mark.parametrize("lead", ["scourge", "speda"])
async def test_collaborators_can_get_live_server_evidence_from_orion(memory, monkeypatch, lead):
    from app.schemas.llm import Block, LLMMessage
    from app.skills import system_ops

    async def host(args, context):
        assert context.agent_id == "orion" and context.triggered_by == "agent"
        assert args == {"action": "exec", "command": "ss -lntp 'sport = :443'"}
        return {"status": "ok", "stdout": "Caddy is listening on TCP 443."}

    def call(name, args):
        return LLMMessage([Block(type="tool_use", id=name, name=name, input=args)], "tool_use")

    monkeypatch.setattr(system_ops, "host_operation", host)
    bits = await setup(memory, [
        tool({"action": "address", "agent_id": "orion", "message": "What is listening on TCP 443?"}),
        reply("Orion, what is listening on TCP 443?"),
        call("system_ops", {"action": "exec", "command": "ss -lntp 'sport = :443'"}),
        reply("Caddy is listening on TCP 443."),
        reply("Orion confirmed that Caddy owns the HTTPS listener."),
    ], contributions=3)
    registry = bits.engine._registry
    await registry.register_skill(system_ops.SystemOpsSkill(None))
    try:
        room = await bits.coordinator.create(1, CreateRoom(lead=lead, members=["orion"]))
        result = await send(bits, room, "Find which service is listening on TCP 443.")
        assert result["status"] == "succeeded"
        calls = bits.client.calls
        assert "ask Orion using collaborate" in calls[0]["system"]
        assert "system_ops" not in {item["name"] for item in calls[0]["tools"]}
        assert "administrator of the live Mark VI server" in calls[2]["system"]
        assert "system_ops" in {item["name"] for item in calls[2]["tools"]}
        assert "Caddy is listening on TCP 443." in str(calls[3]["messages"])
        assert "Caddy is listening on TCP 443." in str(calls[4]["messages"])
    finally:
        await bits.coordinator.shutdown()


async def test_public_question_returns_to_questioner_without_another_owner_input(memory):
    bits = await setup(memory, [
        tool({"action": "address", "agent_id": "ultron",
              "message": "Ultron, what can we establish from our current midterm evidence?"}),
        reply("Ultron, what can we establish from our current midterm evidence?"),
        reply("SPEDA, the March dates are stale. We need this semester's schedule."),
        reply("Agreed, Ultron. We should verify the current schedule before assessing readiness."),
    ], contributions=3)
    room = await bits.coordinator.create(1, CreateRoom(members=["ultron"]))
    result = await send(bits, room, "Okay ask Ultron something")
    assert result["status"] == "succeeded"
    events = (await bits.coordinator.events(1, room["id"]))["events"]
    spoken = [e for e in events if e["kind"] == "message"]
    assert [e["speaker"] for e in spoken] == ["speda", "ultron", "speda"]
    assert len([e for e in events if e["kind"] == "user_message"]) == 1
    request = collaboration_packet(bits.client.calls[2])
    assert request["from_agent"] == "speda" and request["addressed_to"] == "ultron"
    assert "March dates are stale" in str(bits.client.calls[3]["messages"])
    system = bits.client.calls[2]["system"]
    assert '"members": [{"agent_id": "speda"' in system and '"agent_id": "ultron"' in system
    retry = await bits.coordinator.start(1, room["id"], RoomInput(
        input_id="one", message="Okay ask Ultron something"))
    assert retry["duplicate"] and len(bits.client.calls) == 4
    await bits.coordinator.shutdown()


@pytest.mark.parametrize("question", [
    "@Scourge, does your threat model still flag port 5050?",
    "Scourge — with 5050 named as our own Forge gateway: does your threat model still flag it?",
])
async def test_public_message_addresses_peer_without_owner_relay(memory, question):
    bits = await setup(memory, [
        reply(f"Port 5050 is the Forge gateway.\n\n{question}"),
        reply("@Orion, check whether external workers use it before restricting access."),
        reply("I'll check which workers require access."),
    ], contributions=3)
    room = await bits.coordinator.create(1, CreateRoom(members=["orion", "scourge"]))
    adapter = ClientCompatibility(bits.coordinator)
    turn = await completed(bits, adapter, session_id=await session_id(memory, room),
        addressed_agent_id="orion", message="Assess the exposed listener")
    result = await bits.coordinator.inspect_run(1, turn["run_id"])
    assert result["status"] == "succeeded"
    events = (await bits.coordinator.events(1, room["id"]))["events"]
    spoken = [e for e in events if e["kind"] == "message"]
    assert [e["speaker"] for e in spoken] == ["orion", "scourge", "orion"]
    assert len([e for e in events if e["kind"] == "user_message"]) == 1
    queued, = [e for e in events if e["kind"] == "contribution_queued"]
    assert queued["payload"]["reply_to"] == spoken[0]["sequence"]
    assert collaboration_packet(bits.client.calls[1])["collaboration_request"] == question
    assert "external workers" in str(bits.client.calls[2]["messages"])
    stream = await frames(adapter, turn["request_id"])
    assert [f["data"] for f in stream if f["type"] == "room_event" and f["data"]["kind"] == "message"] == spoken
    assert stream[-1]["type"] == "done"
    rows = await adapter.messages(1, turn["session_id"], structured=True)
    assert [r["roomSequence"] for r in rows if r.get("speaker")] == [e["sequence"] for e in spoken]
    await bits.coordinator.shutdown()


async def test_visible_question_does_not_repeat_explicit_address(memory):
    bits = await setup(memory, [
        tool({"action": "address", "agent_id": "scourge", "message": "Assess port 5050."}),
        reply("@Scourge, does your threat model still flag port 5050?"),
        reply("Check which workers require access."), reply("I'll check."),
    ])
    room = await bits.coordinator.create(1, CreateRoom(members=["scourge"]))
    result = await send(bits, room)
    assert result["status"] == "succeeded" and result["contributions"] == 3
    assert len(bits.client.calls) == 4
    await bits.coordinator.shutdown()


@pytest.mark.parametrize(("text", "cap"), [
    ("```text\n@Scourge, check this\n```\n> @Scourge, check that\n@Orion, check as well\n"
     "@Unknown, look here\n@SPEDA, check again\nScourge noted port 5050.", 6),
    ("@Scourge, assess port 5050.", 2),
])
async def test_automatic_address_respects_public_membership_and_round_cap(memory, text, cap):
    bits = await setup(memory, [reply(text)], contributions=cap)
    room = await bits.coordinator.create(1, CreateRoom(members=["scourge"]))
    result = await send(bits, room)
    assert result["status"] == "succeeded" and result["contributions"] == 1
    assert len(result["steps"]) == 1 and len(bits.client.calls) == 1
    await bits.coordinator.shutdown()


@pytest.mark.parametrize("action", ["address", "consult"])
async def test_nested_exchange_finishes_before_outer_caller_responds(memory, action):
    evidence = "The March schedule is historical evidence."
    if action == "consult":
        evidence += "\n@Scourge, can you verify the schedule?"
    bits = await setup(memory, [
        tool({"action": action, "agent_id": "ultron", "message": "Which dates are reliable?"}),
        reply("Ultron, which dates are reliable?"),
        tool({"action": action, "agent_id": "orion", "message": "Which schedule is current?"}),
        reply("Orion, which schedule is current?"),
        reply(evidence),
        reply("SPEDA, the current exam dates remain unverified."),
        reply("Then we should verify this semester's schedule."),
    ], contributions=5 if action == "address" else 7)
    room = await bits.coordinator.create(1, CreateRoom(members=["ultron", "orion"] if action == "address" else ["scourge"]))
    result = await send(bits, room)
    assert result["status"] == "succeeded"
    events = (await bits.coordinator.events(1, room["id"]))["events"]
    starts = [e["speaker"] for e in events if e["kind"] == "speaker_started"]
    assert starts == ["speda", "ultron", "orion", "ultron", "speda"]
    # The outer caller receives the answer after the colleague's own consultation.
    packet = collaboration_packet(bits.client.calls[-1])
    responses = json.loads(packet["collaboration_request"])["peer_replies"]
    assert responses[0]["agent_id"] == "ultron"
    assert responses[0]["answer"] == "SPEDA, the current exam dates remain unverified."
    if action == "consult":
        adapter = ClientCompatibility(bits.coordinator)
        public = await adapter.messages(1, await session_id(memory, room), structured=True)
        assert all(row.get("speaker") not in {"ultron", "orion"} for row in public)
        assert "March schedule is historical" not in json.dumps(public)
    await bits.coordinator.shutdown()


async def test_multiple_peers_share_one_caller_return_inside_contribution_cap(memory):
    bits = await setup(memory, [
        tool({"action": "address", "agent_id": "ultron", "message": "What is the study priority?"},
             {"action": "consult", "agent_id": "orion", "message": "What evidence do we have?"}),
        reply("Ultron, what is the priority? I will also check the evidence."),
        reply("Verify the dates first."), reply("Only the historical schedule is supported."),
        reply("The evidence supports checking the current schedule first."),
    ], contributions=4)
    room = await bits.coordinator.create(1, CreateRoom(members=["ultron"]))
    result = await send(bits, room)
    assert result["status"] == "succeeded" and result["contributions"] == 4
    packet = collaboration_packet(bits.client.calls[-1])
    assert len(json.loads(packet["collaboration_request"])["peer_replies"]) == 2
    assert len(bits.client.calls) == 5
    await bits.coordinator.shutdown()


async def test_public_exchange_reserves_caller_response_before_admitting_peer(memory):
    bits = await setup(memory, [
        tool({"action": "address", "agent_id": "ultron", "message": "Assess the evidence"}),
        reply("Here is my assessment of the available evidence."),
    ], contributions=2)
    room = await bits.coordinator.create(1, CreateRoom(members=["ultron"]))
    result = await send(bits, room)
    assert result["status"] == "succeeded" and len(result["steps"]) == 1
    assert len(bits.client.calls) == 2
    assert "round_limit" in str(bits.client.calls[-1]["messages"])
    await bits.coordinator.shutdown()


async def test_restart_before_caller_response_retains_peer_answer_and_reserved_return(memory):
    bits = await setup(memory, [
        tool({"action": "address", "agent_id": "ultron", "message": "What evidence is current?"}),
        reply("Ultron, what evidence is current?"), reply("The dates need verification."),
        reply("We should verify them before deciding."),
    ], contributions=3)
    original = bits.coordinator._speak
    async def crash_before_return(run_id, step, context):
        if step.mode == "return":
            raise RuntimeError("Executor stopped before caller response")
        return await original(run_id, step, context)
    bits.coordinator._speak = crash_before_return
    room = await bits.coordinator.create(1, CreateRoom(members=["ultron"]))
    result = await send(bits, room)
    assert result["status"] == "reconciling" and len(bits.client.calls) == 3
    bits.coordinator._speak = original
    resumed = await bits.coordinator.resume(1, result["id"])
    await bits.coordinator.live[resumed["run_id"]]
    result = await bits.coordinator.inspect_run(1, result["id"])
    assert result["status"] == "succeeded" and result["contributions"] == 3
    assert len(bits.client.calls) == 4
    assert "dates need verification" in str(bits.client.calls[-1]["messages"])
    await bits.coordinator.shutdown()


async def test_client_stop_also_cancels_waiting_caller_response(memory):
    bits = await setup(memory, [
        tool({"action": "address", "agent_id": "ultron", "message": "Assess this"}),
        reply("Ultron, assess this."), reply("The evidence is incomplete."),
    ])
    adapter = ClientCompatibility(bits.coordinator)
    room = await bits.coordinator.create(1, CreateRoom(members=["ultron"]))
    # Gate the peer's model call after the root has reserved its response.
    entered, release = asyncio.Event(), asyncio.Event()
    original = bits.client.stream_message
    @asynccontextmanager
    async def gate(**args):
        if "You are Ultron" in args["system"]:
            entered.set()
            await release.wait()
        async with original(**args) as stream:
            yield stream
    bits.client.stream_message = gate
    request_id = str(uuid4())
    first = await adapter.start(1, "speda", ClientChat(request_id=request_id,
        session_id=await session_id(memory, room), message="Assess this"))
    await asyncio.wait_for(entered.wait(), 5)
    task = bits.coordinator.live[first["run_id"]]
    stopped = await adapter.cancel(1, request_id)
    release.set()
    await asyncio.wait_for(task, 5)
    assert stopped["cancelled"]
    async with memory.sessions() as db:
        steps = list(await db.scalars(select(AgentStep).where(AgentStep.run_id == first["run_id"])))
    assert all(s.status == "cancelled" for s in steps if s.operation_id.startswith("return:"))
    assert len(bits.client.calls) == 2
    await bits.coordinator.shutdown()
