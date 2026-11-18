import json
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.models.automation import Automation
from app.core.registry import CapabilityRegistry
from app.models.coordination import CoordinationRun, RoomEvent
from app.models.iacon import IaconControl
from app.models.memory_serving import MemoryChange
from app.models.message import Message
from app.schemas.coordination import CreateRoom
from app.schemas.llm import Block, ProviderError
from app.schemas.iacon import MaintenancePolicy
from app.schemas.trigger import TriggerRequest
from app.services.automation_admission import AutomationAdmissions
from app.services.iacon_catalogue import Catalogue
from app.services.iacon_worker import Orion
from app.services.memory_runtime import MemoryRuntime
from app.services.watch_attention import disposition
from app.skills.memory_lookup import MemoryLookupSkill
from sqlalchemy import select
from tests.test_coordination import reply, send, setup, tool
from tests.test_history_lookup import seed
from tests.test_iacon import FakeClient, effective, funded


async def test_recent_conversation_survives_full_memory_timeout(memory, monkeypatch):
    _, identity, _ = await seed(memory, "Statistics exam is at three; I'm betting on you.",
                                agent="friday", role="assistant")
    await seed(memory, "Statistics hidden by owner.", agent="friday", owner=2)

    async def unavailable(*args, **kwargs):
        raise TimeoutError()

    monkeypatch.setattr(memory.store, "read", unavailable)
    runtime = MemoryRuntime(memory.store, prepare_timeout=.5)
    bits = await setup(memory, [reply("I said I was betting on you.")])
    bits.engine._memory_runtime = runtime
    await bits.engine._registry.register_skill(MemoryLookupSkill(runtime))
    try:
        room = await bits.coordinator.create(1, CreateRoom(lead="friday"))
        await send(bits, room, "Remember our earlier statistics discussion?")
        system = bits.client.calls[0]["system"]
        block = system.split("<recent_conversation_evidence>\n", 1)[1].split("\n</recent_conversation_evidence>", 1)[0]
        sources = json.loads(block)
        assert len(bits.client.calls) == 1 and [item["message_id"] for item in sources] == [identity]
        assert sources[0]["attribution"] == "assistant_statement_not_owner_assertion"
    finally:
        await bits.coordinator.shutdown()


async def test_maintenance_model_is_priced_locally_and_rollback_compatible(memory):
    client = FakeClient()
    budget = await funded(memory, client)
    version, policy = await budget.policy(1)
    with pytest.raises(ValueError, match="conservative rate"):
        await budget.save(1, version, policy.model_copy(update={"model": "openai:unrated"}))
    saved = await budget.save(1, version, policy.model_copy(update={"model": "kimi:test"}))
    assert (await budget.policy(1))[1].model == "kimi:test"
    async with memory.sessions() as db:
        control = await db.get(IaconControl, 1)
        assert "model" not in control.policy
        assert MaintenancePolicy.model_validate(control.policy).prices == policy.prices
    await budget.call(memory.context(), "new-source", policy=effective(), system="Interpret", messages=[])
    status = await budget.status(1)
    assert len(client.calls) == 1
    assert status["calls"][0]["route"]["maintenance_revision"] == saved["version"]


async def test_pending_source_resumes_when_maintenance_route_is_corrected(memory):
    from app.schemas.llm import ModelSpec

    class ModelPolicy:
        async def resolve(self, *, model=None, **kwargs):
            return replace(effective(), spec=ModelSpec(ref=model or "kimi:unrated", protocol="chat"))

    _, _, source_id = await seed(memory, "The workshop is on Friday.")
    client = FakeClient()
    budget = await funded(memory, client)
    orion = Orion(Catalogue(memory.store), budget, ModelPolicy(), lambda stage: stage)
    assert (await orion.maintenance_health(1))["status"] == "missing_rate"
    assert (await orion.drain(memory.context()))["outcomes"][0]["status"] == "pending"
    assert not client.calls
    version, policy = await budget.policy(1)
    await budget.save(1, version, policy.model_copy(update={"model": "kimi:test"}))
    assert (await orion.drain(memory.context()))["outcomes"][0]["status"] == "done"
    async with memory.sessions() as db:
        assert (await db.get(MemoryChange, source_id)).outcome == "iacon_interpreted"
    assert len(client.calls) == 1


async def test_collaboration_capacity_is_a_partial_outcome(memory):
    bits = await setup(memory, [
        tool({"action": "address", "agent_id": "ultron", "message": "Review the proposal"}),
        reply("I could not get the requested review."),
    ], contributions=1)
    try:
        room = await bits.coordinator.create(1, CreateRoom(members=["ultron"]))
        result = await send(bits, room, "Get Ultron's review")
        assert result["status"] == "succeeded"
        assert result["outcome"] == {
            "state": "partial", "effects": [], "gaps": [{"tool": "collaborate", "reason": "round_limit"}]}
    finally:
        await bits.coordinator.shutdown()


async def test_followup_sees_unfinished_work_in_its_room(memory):
    bits = await setup(memory, [reply("I will check."), reply("The result is still unconfirmed.")])
    try:
        room = await bits.coordinator.create(1, CreateRoom())
        original = await send(bits, room, "Check my calendar changes.")
        async with memory.sessions() as db, db.begin():
            row = await db.get(CoordinationRun, original["id"])
            row.status = "reconciling"
        await send(bits, room, "What happened with that?", input_id="followup")
        system = bits.client.calls[-1]["system"]
        assert original["id"] in system and '"state": "reconciling"' in system
        assert len(bits.client.calls) == 2
    finally:
        await bits.coordinator.shutdown()


@pytest.mark.parametrize("lose_response", [False, True])
async def test_calendar_effect_is_held_or_reused_with_a_new_tool_call_id(memory, monkeypatch, lose_response):
    registry = CapabilityRegistry(memory.sessions)
    monkeypatch.setattr(registry, "validate_call", lambda call, context: None)
    monkeypatch.setattr(registry, "_mail_calendar_tool", lambda name: name == "calendar_create_event")
    executions = []

    async def create(name, args, context):
        executions.append(args)
        if lose_response:
            raise TimeoutError("The remote acknowledgement was lost")
        return "Event 'Study' created [event-1]."

    monkeypatch.setattr(registry, "execute", create)
    context = memory.context()
    context.run_id = "same-owner-run"
    first = Block(type="tool_use", id="first", name="calendar_create_event", input={"summary": "Study"})
    retry = Block(type="tool_use", id="different", name="calendar_create_event",
                  input={"summary": "Study", "calendar_id": "primary"})
    if lose_response:
        with pytest.raises(TimeoutError):
            await registry.execute_outcome(first, context)
        with pytest.raises(ProviderError, match="unknown outcome"):
            await registry.execute_outcome(retry, context)
    else:
        accepted = await registry.execute_outcome(first, context)
        assert accepted.observation.reference == "event-1"
        assert await registry.execute_outcome(retry, context) == accepted
    assert len(executions) == 1


@pytest.mark.parametrize(("proposal", "expected"), [
    ('{"disposition":"silent"}', None),
    ('{"disposition":"notify","notice":"The workshop moved to 12:00. Source: organizer email."}',
     "The workshop moved to 12:00. Source: organizer email."),
    ('Plain text proposal with no delivery decision', None),
])
async def test_attention_watch_delivers_only_a_valid_notice(memory, proposal, expected):
    bits = await setup(memory, [reply(proposal)])
    service = AutomationAdmissions(bits.coordinator)
    async with memory.sessions() as db, db.begin():
        db.add(Automation(public_key="attention-watch", user_id=1, agent_id="speda",
            request_id="watch", request_hash="watch", name="Watch", timezone="Europe/Istanbul",
            active=True, spec={"kind": "mail_watch", "attention_only": True, "attention_quiet": [0, 0]}))

    async def admit(owner, agent, body, **kwargs):
        return body

    bits.coordinator.automations = SimpleNamespace(admitted_payload=admit)
    body = TriggerRequest(payload={"type": "mail_watch", "automation_key": "attention-watch",
        "automation_revision": 1, "attention_only": True, "mail_calendar_create": False},
        output_mode="push", idempotency_key="watch-event")
    try:
        result = await service.start(1, "speda", body)
        task = bits.coordinator.live.get(result["run_id"])
        if task:
            await task
        assert (await service.start(1, "speda", body))["run_id"] == result["run_id"]
        assert len(bits.client.calls) == 1
        async with memory.sessions() as db:
            output = await db.scalar(select(Message).where(Message.role == "assistant"))
            assert (output.content[0]["text"] if output else None) == expected
            assert await db.scalar(select(RoomEvent).where(RoomEvent.kind == "delta")) is None
        if expected is None:
            assert (await service.inspect(1, result["request_id"]))["delivery_status"] == "suppressed"
    finally:
        await bits.coordinator.shutdown()


async def test_attention_watch_suppresses_quiet_hours_and_recheck_of_changed_revision(memory):
    async with memory.sessions() as db, db.begin():
        watch = Automation(public_key="watch", user_id=1, agent_id="speda", request_id="watch",
            request_hash="watch", name="Watch", timezone="Europe/Istanbul", active=True,
            spec={"kind": "mail_watch", "attention_only": True})
        db.add(watch)
        await db.flush()
        now = datetime(2026, 11, 18, 20, 30, tzinfo=timezone.utc)
        run = SimpleNamespace(user_id=1, created_at=now, policy={
            "trigger_payload": {"automation_key": "watch", "automation_revision": 1}})
        proposal = '{"disposition":"notify","notice":"An urgent change"}'
        assert await disposition(db, run, proposal, now=now) == ("", "quiet_hours")
        watch.spec = {**watch.spec, "attention_snoozed_until": "2026-11-19T08:00:00+03:00"}
        await db.flush()
        assert await disposition(db, run, proposal, now=now) == ("", "snoozed")
        watch.spec = {**watch.spec, "attention_snoozed_until": None, "attention_quiet": [0, 0]}
        run.policy["trigger_payload"]["attention_shadow"] = True
        await db.flush()
        assert await disposition(db, run, proposal, now=now) == ("", "would_notify")
        watch.content_version += 1
        await db.flush()
        assert await disposition(db, run, proposal, now=now) == ("", "watch_changed")
