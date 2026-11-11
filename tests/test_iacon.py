import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from time import monotonic
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo
import httpx
import pytest
from sqlalchemy import select
from app.models.delivery import TelegramLink, TelegramOwner
from app.models.iacon import IaconCall, IaconFollowup, IaconRecord, IaconWork
from app.models.memory_serving import MemoryChange
from app.schemas.iacon import Command, MaintenancePolicy
from app.schemas.llm import EffectivePolicy, ModelSpec, ThinkingRequest, Block, LLMMessage
from app.services.iacon_catalogue import Catalogue, Conflict
from app.services.iacon_budget import MaintenanceBudget, BudgetDenied
from app.services.iacon_worker import Orion
from app.services.iacon_telegram import TelegramFollowups
from app.services.memory_runtime import MemoryRuntime, packet_json
from app.services.memory_store import capture_source
from app.profiles.registry import ProfileRegistry
from app.services.coordination_budget import CoordinationError
from app.services.telegram_gateway import TelegramGateway


def effective():
    return EffectivePolicy(spec=ModelSpec(ref="kimi:test", protocol="chat"), requested=ThinkingRequest(),
        wire={}, source="test", revision=1, show_thinking=False, deadline=monotonic()+100,
        fallbacks=("openai:other",))


async def command(memory, text="I prefer tea", **changes):
    async with memory.sessions() as db, db.begin():
        source = await capture_source(db, user_id=1, source_key=f"test:{uuid4()}", content=text)
        sid = source.id
    values = dict(command_id=uuid4().hex, kind="preference", title="Drinks",
        payload={"text": text}, evidence=[{"source_id": sid, "start": 0, "end": len(text), "quote": text}])
    values.update(changes)
    return Command(**values)


async def test_atomic_correction_history_idempotency_and_foreground(memory):
    catalogue = Catalogue(memory.store)
    first = await command(memory)
    saved = await catalogue.apply(1, first)
    assert await catalogue.apply(1, first) == saved
    update = await command(memory, "I now prefer coffee", record_id=saved["id"], expected_version=1)
    corrected = await catalogue.apply(1, update)
    assert corrected["version"] == 2
    with pytest.raises(Conflict):
        await catalogue.apply(1, update.model_copy(update={"command_id": "stale"}))
    record = await catalogue.get(1, saved["id"], history=True)
    assert len(record["revisions"]) == 2
    packet = await MemoryRuntime(memory.store).prepare(memory.context("Drinks coffee"))
    assert any('coffee' in b and 'canonical_record' in b for b in packet.blocks)
    async with memory.sessions() as db:
        original = await db.get(MemoryChange, first.evidence[0].source_id)
        assert original.content == "I prefer tea" and not original.revoked


async def test_owner_scope_quote_authority_and_dependency_revocation(memory):
    catalogue = Catalogue(memory.store)
    c = await command(memory)
    with pytest.raises(ValueError):
        await catalogue.apply(2, c)
    row = await catalogue.apply(1, c)
    await memory.store.revoke(memory.context(), c.evidence[0].source_id)
    packet = await MemoryRuntime(memory.store).prepare(memory.context("tea Drinks"))
    assert not any(row["id"] in b for b in packet.blocks)
    sid, _ = await memory.seed("Quoted opinion", role="quoted", publish=False)
    c = Command(command_id="quoted", kind="claim", title="Opinion", payload={"text": "Quoted opinion"},
        evidence=[{"source_id": sid, "start": 0, "end": 14, "quote": "Quoted opinion"}])
    with pytest.raises(ValueError, match="Owner reports"):
        await catalogue.apply(1, c)


class FakeClient:
    def __init__(self, outputs=None, fail=False):
        self.calls = []
        self.outputs = outputs or ['[]']
        self.fail = fail

    async def create_message(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise TimeoutError()
        raw = self.outputs.pop(0)
        return LLMMessage(content=[Block(type="text", text=raw)], stop_reason="end_turn", usage={"input_tokens": 10})


async def funded(memory, client, **changes):
    budget = MaintenanceBudget(memory.sessions, client)
    limits = dict(daily_calls=20, daily_tokens=500000, daily_microdollars=500000,
                  prices={"kimi:test": 1})
    limits.update(changes)
    await budget.save(1, 0, MaintenancePolicy(**limits))
    return budget


async def test_default_zero_budget_and_hard_concurrent_ceiling(memory):
    client = FakeClient()
    budget = MaintenanceBudget(memory.sessions, client)
    with pytest.raises(BudgetDenied):
        await budget.call(memory.context(), "zero", policy=effective(), system="test", messages=[])
    assert not client.calls
    budget = await funded(memory, client, daily_calls=1)
    results = await asyncio.gather(*(budget.reserve(memory.context(), f"job:{i}", effective(), 100)
                                     for i in range(3)), return_exceptions=True)
    assert len([r for r in results if isinstance(r, str)]) == 1
    status = await budget.status(1)
    assert status["reservations"][0]["calls"] == 1


async def test_paid_receipt_replay_pause_and_unknown_outcome(memory):
    client = FakeClient()
    budget = await funded(memory, client)
    for _ in range(2):
        await budget.call(memory.context(), "one", policy=effective(), system="test", messages=[])
    assert len(client.calls) == 1 and client.calls[0]["policy"].fallbacks == ()
    client.fail = True
    with pytest.raises(TimeoutError):
        await budget.call(memory.context(), "unknown", policy=effective(), system="test", messages=[])
    with pytest.raises(BudgetDenied, match="reconciliation"):
        await budget.call(memory.context(), "unknown", policy=effective(), system="test", messages=[])
    version, policy = await budget.policy(1)
    await budget.save(1, version, policy.model_copy(update={"paused": True}))
    restarted = MaintenanceBudget(memory.sessions, client)
    with pytest.raises(BudgetDenied):
        await restarted.call(memory.context(), "after_restart", policy=effective(), system="test", messages=[])
    assert len(client.calls) == 2
    assert {c["route"]["model"] for c in (await restarted.status(1))["calls"]} == {"kimi:test"}


async def test_worker_two_stages_and_invalid_output_never_retried(memory):
    c = await command(memory)
    client = FakeClient([json.dumps([c.model_dump(mode="json")]), '{"accept":true,"reason":"Supported"}'])
    budget = await funded(memory, client)
    class Policy:
        async def resolve(self, **kwargs):
            return effective()
    catalogue = Catalogue(memory.store)
    worker = Orion(catalogue, budget, Policy(), lambda stage: stage)
    result = await worker.drain(memory.context())
    assert result["processed"] == 2
    assert len(await catalogue.list(1)) == 1
    assert len(client.calls) == 2
    await worker.drain(memory.context())
    assert len(client.calls) == 2
    await command(memory, "New experience")
    client.outputs = ["{broken json"]
    assert (await worker.drain(memory.context()))["outcomes"][0]["status"] == "quarantined"
    await worker.drain(memory.context())
    assert len(client.calls) == 3


async def state_setup(memory, transport):
    today = datetime.now(timezone.utc).date()
    c = await command(memory, "I am waiting for my application result", kind="state", title="Application",
        payload={"summary": "Waiting for application result", "status": "waiting", "review_on": str(today),
                 "verified_on": str(today), "question": "Did the application result arrive?"})
    catalogue = Catalogue(memory.store)
    saved = await catalogue.apply(1, c)
    async with memory.sessions() as db, db.begin():
        source = await db.get(MemoryChange, c.evidence[0].source_id)
        source.outcome = "iacon_interpreted"
    budget = await funded(memory, FakeClient(), telegram_enabled=True, quiet_start=0, quiet_end=0)
    config = SimpleNamespace(iacon_telegram_token="test-token", iacon_telegram_chat_id=123, iacon_telegram_owner_id=456)
    telegram = TelegramFollowups(catalogue, budget, config, transport=httpx.MockTransport(transport))
    return catalogue, saved, telegram


async def test_current_incident_has_priority_and_older_states_remain_retrievable(memory):
    ctx = memory.context("What are my sisters' names?")
    today = datetime.now(ZoneInfo(ctx.timezone)).date()
    yesterday = today - timedelta(days=1)
    catalogue = Catalogue(memory.store)

    async def save(title, salience, status, verified, review, **dates):
        draft = await command(memory, title, kind="state", title=title,
            payload={"summary": title, "status": status, "salience": salience, "question": "Is this still current?",
                     "verified_on": str(verified), "review_on": str(review), **dates})
        return await catalogue.apply(1, draft)

    old = await save("Photography classes", "normal", "active", yesterday, yesterday)
    overdue = await save("Unresolved urgent outage", "high", "active", yesterday, yesterday)
    incident = await save("Current server incident", "high", "active", today, today + timedelta(days=1))
    waiting = await save("Insurance paperwork", "high", "waiting", today, today + timedelta(days=1))
    await save("Upcoming trip", "high", "planned", today, today + timedelta(days=1),
               starts_on=str(today + timedelta(days=4)))
    runtime = MemoryRuntime(memory.store, prepare_timeout=2)
    packet = await runtime.prepare(ctx)
    states = json.loads(packet_json(packet))["current_states"]
    assert [row["record_id"] for row in states] == [incident["id"], overdue["id"], waiting["id"]]
    assert [row["freshness"] for row in states] == ["confirmed", "review_due", "confirmed"]
    assert packet.token_upper_bound <= ctx.memory_budget.automatic_limit
    lookup = await runtime.lookup(memory.context(), "photography classes")
    assert old["id"] in packet_json(lookup) and "current status unverified" in packet_json(lookup)
    from app.schemas.coordination import CreateRoom
    from tests.test_coordination import setup, reply, send

    bits = await setup(memory, [reply("Hello"), reply("Here is the schedule")])
    bits.engine._memory_runtime = runtime
    try:
        room = await bits.coordinator.create(1, CreateRoom())
        assert (await send(bits, room, "Hello"))["status"] == "succeeded"
        assert (await send(bits, room, "Show my calendar", input_id="two"))["status"] == "succeeded"
        for call in bits.client.calls:
            raw = call["system"].split("<remembered_context>\n", 1)[1].split("\n</remembered_context>", 1)[0]
            assert json.loads(raw)["current_states"][0]["record_id"] == incident["id"]
    finally:
        await bits.coordinator.shutdown()
    await memory.store.revoke(ctx, incident["evidence"][0]["source_id"])
    after = await runtime.prepare(memory.context("Hello"))
    assert [row["record_id"] for row in json.loads(packet_json(after))["current_states"]] == [overdue["id"], waiting["id"]]


async def test_orion_interprets_current_incident_and_reviews_it_ahead_of_older_work(memory):
    ctx = memory.context()
    today = datetime.now(ZoneInfo(ctx.timezone)).date()
    routine = await command(memory)
    incident = await command(memory, "The server outage is ongoing", kind="state", title="Server outage",
        payload={"summary": "The server outage is ongoing", "status": "active", "salience": "high",
                 "verified_on": str(today), "review_on": str(today), "question": "Is the outage resolved?"})
    ctx.memory_source_id = incident.evidence[0].source_id
    client = FakeClient([json.dumps([incident.model_dump(mode="json")]),
        '{"accept":true,"reason":"Owner confirmed an ongoing incident"}'])
    budget = await funded(memory, client)

    class Policy:
        async def resolve(self, **kwargs):
            return effective()

    catalogue = Catalogue(memory.store)
    orion = Orion(catalogue, budget, Policy(), lambda stage: stage)
    await orion.propose(ctx, routine)
    assert (await orion.drain(ctx, limit=2))["processed"] == 2
    assert [row["title"] for row in await catalogue.list(1)] == ["Server outage"]
    assert len(client.calls) == 2


async def test_native_orion_telegram_prioritizes_and_closes_exact_state(memory):
    today = datetime.now(ZoneInfo("Europe/Istanbul")).date()
    now = datetime.now(timezone.utc)
    catalogue = Catalogue(memory.store)
    old_date = today - timedelta(days=3)
    old = await command(memory, "Old routine review", kind="state", title="Old routine review",
        payload={"summary": "Old routine review", "status": "waiting", "verified_on": str(old_date),
                 "review_on": str(old_date), "question": "Is the old review current?"})
    await catalogue.apply(1, old)
    incident = await command(memory, "Active server incident", kind="state", title="Active server incident",
        payload={"summary": "Active server incident", "status": "active", "salience": "high",
                 "verified_on": str(old_date), "review_on": str(old_date + timedelta(days=1)),
                 "question": "Is the incident ongoing?"})
    saved = await catalogue.apply(1, incident)
    async with memory.sessions() as db, db.begin():
        db.add(TelegramOwner(user_id=1, sender_id=456))
        db.add(TelegramLink(user_id=1, agent_id="orion", chat_id=456))

    outbound = []

    class NativeDelivery:
        def token(self, agent):
            return "fixture" if agent == "orion" else ""

        async def telegram(self, agent, method, payload):
            outbound.append((agent, method, payload))
            return {"message_id": 80}

    deliveries = NativeDelivery()
    budget = await funded(memory, FakeClient(), telegram_enabled=True, quiet_start=0, quiet_end=0)
    config = SimpleNamespace(iacon_telegram_token="", iacon_telegram_chat_id=0, iacon_telegram_owner_id=0,
        telegram_owner_id=0, owner_timezone="Europe/Istanbul")
    followups = TelegramFollowups(catalogue, budget, config, deliveries=deliveries)
    gateway = TelegramGateway(SimpleNamespace(sessions=memory.sessions, profiles=ProfileRegistry()),
        deliveries, config, iacon_followups=followups)
    try:
        sent = await followups.send_due(memory.context(), now=now)
        assert sent["status"] == "sent"
        assert outbound[0][:2] == ("orion", "sendMessage")
        assert outbound[0][2]["chat_id"] == 456
        assert "Is the incident ongoing?" in outbound[0][2]["text"]
        event = {"update_id": 12, "callback_query": {"id": "click", "from": {"id": 999},
            "message": {"message_id": 80, "chat": {"id": 456, "type": "private"}},
            "data": f'ia:{sent["id"]}:done'}}
        with pytest.raises(CoordinationError, match="linked Orion owner"):
            await gateway.receive(1, "orion", event)
        event["callback_query"]["from"]["id"] = 456
        event["callback_query"]["message"]["message_id"] = 81
        with pytest.raises(CoordinationError, match="recorded Telegram message"):
            await gateway.receive(1, "orion", event)
        event["callback_query"]["message"]["message_id"] = 80
        written = {"update_id": 13, "message": {"from": {"id": 456},
            "chat": {"id": 456, "type": "private"}, "text": "It is still ongoing.",
            "reply_to_message": {"message_id": 80, "from": {"is_bot": True},
                                 "text": outbound[0][2]["text"]}}}
        queued = await gateway.receive(1, "orion", written)
        assert queued["status"] == "pending_review" and queued["state_id"] == saved["id"]
        result = await gateway.receive(1, "orion", event)
        assert result["id"] == saved["id"] and result["payload"]["status"] == "completed"
        assert outbound[-1][:2] == ("orion", "answerCallbackQuery")
        assert (await gateway.receive(1, "orion", event)) == result
        assert (await catalogue.get(1, saved["id"]))["version"] == 2
        packet = await MemoryRuntime(memory.store, prepare_timeout=2).prepare(memory.context("Hello"))
        assert all(state["record_id"] != saved["id"] for state in json.loads(packet_json(packet))["current_states"])
    finally:
        await followups.close()


async def test_telegram_silence_duplicate_completion_and_late_reply(memory):
    sent = []
    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 80}})
    catalogue, state, telegram = await state_setup(memory, handler)
    try:
        first = await telegram.send_due(memory.context())
        assert first["status"] == "sent"
        assert (await telegram.send_due(memory.context()))["status"] == "cooldown"
        assert (await catalogue.get(1, state["id"]))["payload"]["status"] == "waiting"
        event = {"update_id": 1, "callback_query": {"from": {"id": 456},
            "message": {"message_id": 80, "chat": {"id": 123}}, "data": f'ia:{first["id"]}:done'}}
        result = await telegram.receive(memory.context(), event)
        assert result["payload"]["status"] == "completed"
        assert await telegram.receive(memory.context(), event) == result
        event["update_id"] = 2
        assert (await telegram.receive(memory.context(), event))["status"] == "stale_reply"
        assert len(sent) == 1
    finally:
        await telegram.close()


async def test_unknown_delivery_never_resends_and_free_reply_is_not_completion(memory):
    def handler(request):
        raise httpx.ReadTimeout("unknown")
    catalogue, state, telegram = await state_setup(memory, handler)
    try:
        delivery = await telegram.send_due(memory.context())
        assert delivery["status"] == "delivery_unknown"
        assert (await telegram.send_due(memory.context(), now=datetime.now(timezone.utc)+timedelta(days=2)))["status"] == "nothing_due"
        event = {"update_id": 10, "message": {"from": {"id": 456}, "chat": {"id": 123},
            "text": "No, the result has not arrived.", "reply_to_message": {"text": f'[Iacon:{delivery["id"]}]'}}}
        assert (await telegram.receive(memory.context(), event))["status"] == "pending_review"
        assert (await catalogue.get(1, state["id"]))["payload"]["status"] == "waiting"
        event["message"]["from"]["id"] = 999
        with pytest.raises(ValueError):
            await telegram.receive(memory.context(), event)
    finally:
        await telegram.close()


async def test_restart_reuses_completed_review_and_operator_cannot_replay_unknown(memory):
    from app.schemas.iacon import WorkResolution
    c = await command(memory)
    client = FakeClient(['{"accept":true,"reason":"Owner evidence"}'])
    budget = await funded(memory, client)
    class Policy:
        async def resolve(self, **kwargs):
            return effective()
    catalogue = Catalogue(memory.store)
    worker = Orion(catalogue, budget, Policy(), lambda stage: stage)
    await worker.propose(memory.context(), c)
    async with memory.sessions() as db, db.begin():
        source = await db.get(MemoryChange, c.evidence[0].source_id)
        source.outcome = "source_only"
        job = (await db.execute(select(IaconWork).where(IaconWork.kind == "review"))).scalar_one()
        job.status, job.claim = "running", "old-process"
        job.available_at = datetime.now(timezone.utc)-timedelta(minutes=1)
        job_id = job.id
    await budget.call(memory.context(), job_id, policy=effective(), system="review", messages=[])
    restarted = Orion(catalogue, MaintenanceBudget(memory.sessions, client), Policy(), lambda stage: stage)
    result = await restarted.drain(memory.context())
    assert result["outcomes"][0]["status"] == "done"
    assert len(await catalogue.list(1)) == 1 and len(client.calls) == 1
    async with memory.sessions() as db, db.begin():
        db.add(IaconWork(id="held-unit", user_id=1, work_key="held-unit", kind="review", payload={}, status="held"))
    await budget.reserve(memory.context(), "held-unit", effective(), 100)
    with pytest.raises(ValueError, match="Unknown"):
        await restarted.resolve(1, "held-unit", WorkResolution(expected_status="held", action="resume_saved"))


async def test_links_conflicts_and_native_detail_share_bounded_lookup(memory):
    from app.skills.memory_lookup import MemoryLookupSkill
    catalogue = Catalogue(memory.store)
    person = await catalogue.apply(1, await command(memory, "Nora is my colleague", kind="entity", title="Nora",
        payload={"entity_type": "person"}))
    event = await catalogue.apply(1, await command(memory, "We met Tuesday", kind="episode", title="Meeting",
        payload={"description": "We met Tuesday", "time_wording": "Tuesday"},
        links=[{"target_id": person["id"], "relation": "participant"}]))
    packet = await MemoryRuntime(memory.store).prepare(memory.context("Nora Tuesday"))
    assert any(event["id"] in b for b in packet.blocks)
    for text in ["I prefer tea", "I prefer coffee"]:
        await catalogue.apply(1, await command(memory, text, kind="claim", payload={"text": text, "conflict_group": "drinks"}))
    rows = await catalogue.list(1, kind="claim")
    await catalogue.refresh(1, rows[0]["id"])
    packet = await MemoryRuntime(memory.store).prepare(memory.context("Drinks coffee"))
    assert any('unresolved_conflicts' in b and rows[1]["id"] in b for b in packet.blocks)
    doc = await catalogue.apply(1, await command(memory, "# Native ledger", kind="document", title="Ledger",
        payload={"content": "# Native ledger\n\n" + "| original row | 42 |\n" * 1000}))
    context = memory.context()
    skill = MemoryLookupSkill(MemoryRuntime(memory.store), catalogue=catalogue)
    detail = json.loads(await skill.execute({"query": "ledger", "record_id": doc["id"]}, context))
    assert detail["next_offset"] and "original row" in detail["text"]
    assert context.memory_budget.used <= context.memory_budget.lookup_limit


async def test_invalid_thinking_request_rejected_before_cost_admission(memory):
    client = FakeClient()
    budget = await funded(memory, client)
    policy = replace(effective(), wire={"thinking": {"type": "enabled", "budget_tokens": 9000}})
    with pytest.raises(ValueError, match="Thinking budget"):
        await budget.call(memory.context(), "bad-thinking", policy=policy, system="test", messages=[], max_tokens=1024)
    assert not client.calls and not (await budget.status(1))["reservations"]


def test_write_contract_is_explicit_but_not_in_everyday_answer_context(memory):
    from app.skills.iacon import IaconProposeSkill
    from jsonschema import Draft202012Validator
    skill = IaconProposeSkill(None)
    assert not skill.available(memory.context())
    assert skill.available(memory.context(agent_id="orion"))
    schema = Command.model_json_schema()
    Draft202012Validator.check_schema(schema)
    branches = schema["properties"]["payload"]["oneOf"]
    state = next(b for b in branches if b["title"] == "State")
    assert {"review_on", "verified_on", "question", "status"}.issubset(state["required"])
