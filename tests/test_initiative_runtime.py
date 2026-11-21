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


@pytest.mark.parametrize("opening", ["phewwww", "😮‍💨"])
async def test_fresh_reaction_receives_recent_exam_on_first_call(memory, opening):
    _, identity, _ = await seed(memory, "Going into my accounting exam now.", agent="ultron")
    await seed(memory, "Private context about tomorrow", owner=2, agent="ultron")
    await seed(memory, "Other agent private bridge", agent="friday")
    bits = await setup(memory, [reply("How did the exam go?")])
    runtime = MemoryRuntime(memory.store)
    bits.engine._memory_runtime = runtime
    await bits.engine._registry.register_skill(MemoryLookupSkill(runtime))
    try:
        room = await bits.coordinator.create(1, CreateRoom(lead="ultron"))
        await send(bits, room, opening)
        prompt = bits.client.calls[0]["system"]
        assert "Other agent private bridge" not in prompt
        evidence = json.loads(prompt.split("<recent_conversation_evidence>")[1].split("</recent_conversation_evidence>")[0])
        assert [item["message_id"] for item in evidence] == [identity]
        assert len(bits.client.calls) == 1
        from app.models.coordination import StepEpisodeReferences
        async with memory.sessions() as db:
            refs = await db.scalar(select(StepEpisodeReferences))
            assert refs.spans[0][0] == identity
    finally:
        await bits.coordinator.shutdown()


async def test_resume_rechecks_archive_visibility(memory):
    from app.models.client_compat import HiddenMessage
    session, mid, _ = await seed(memory, "Exam this morning", agent="ultron")
    runtime = MemoryRuntime(memory.store)
    context = memory.context("phewwww", agent_id="ultron", allowed_tools=frozenset({"memory_lookup"}))
    refs = [[mid, 0, 4]]
    assert (await runtime.restore_recent(context, refs))[0]["text"] == "Exam"
    async with memory.sessions() as db, db.begin():
        db.add(HiddenMessage(session_id=session, message_id=mid, request_id="hide"))
    assert await runtime.restore_recent(context, refs) == []


async def test_temporal_episode_skips_morning_briefing_and_newer_chat(memory):
    from app.services.history_lookup import search_archive
    _, target, _ = await seed(memory, "We agreed to review the draft.", agent="friday",
                              stamp=datetime(2026, 11, 19, 20))
    await seed(memory, "Last night was cold.", agent="friday", stamp=datetime(2026, 11, 20, 8))
    ctx = memory.context(agent_id="friday", prompt_datetime="2026-11-20T12:00:00Z")
    async with memory.sessions() as db:
        packet = await search_archive(db, ctx, "remember last night?", recent=True, continuity=True)
    assert [item["message_id"] for item in packet["results"]] == [target]


def test_calendar_source_quote_must_come_from_available_evidence(memory):
    registry = CapabilityRegistry()
    context = memory.context("Choose an open afternoon for my study session.")
    registry._check_calendar_evidence({"timing_evidence": "Choose an open afternoon"}, context)
    with pytest.raises(ProviderError, match="lacks supporting evidence"):
        registry._check_calendar_evidence({"timing_evidence": "My return departs at 17:00"}, context)


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


@pytest.mark.parametrize("kind", ["ask", "digest"])
async def test_attention_items_validate_sources_dedupe_and_resolve(memory, kind):
    from app.services.watch_attention import active_items
    from app.skills.resolve_followup import ResolveFollowup

    now = datetime.now(timezone.utc)
    async with memory.sessions() as db, db.begin():
        db.add(Automation(public_key="sources", user_id=1, agent_id="speda", request_id="sources",
                          request_hash="sources", name="Watch", timezone="UTC", active=True,
                          spec={"kind": "mail_watch", "attention_only": True, "attention_quiet": [0, 0]}))
        await db.flush()
        run = SimpleNamespace(user_id=1, created_at=now, policy={"trigger_payload": {
            "automation_key": "sources", "automation_revision": 1, "event_data": {"messages": [{"id": "mail1"}]}}})
        candidate = {"disposition": kind, "notice": "What day will you return?", "source_ids": ["unknown"]}
        assert (await disposition(db, run, json.dumps(candidate), now=now))[1] == "invalid_sources"
        candidate["source_ids"] = ["mail1"]
        outcome = await disposition(db, run, json.dumps(candidate), now=now)
        assert outcome == (candidate["notice"] if kind == "ask" else "", kind)
        assert (await disposition(db, run, json.dumps(candidate), now=now))[1] == "duplicate"
        cutoff = now.replace(tzinfo=None)
        items = await active_items(db, 1, "speda", kind, cutoff)
        assert len(items) == 1 and not await active_items(db, 1, "friday", kind, cutoff)
        key = items[0].key
    if kind == "ask":
        context = memory.context("Wednesday")
        context.extra["open_attention_questions"] = [{"id": key}]
        result = await ResolveFollowup(memory.sessions).execute({"question_id": key, "status": "answered"}, context)
        assert json.loads(result)["status"] == "answered"
        async with memory.sessions() as db:
            assert not await active_items(db, 1, "speda", "ask", cutoff)


@pytest.mark.parametrize(("message", "withdrawn"), [
    ("Oh for gods sake. Forget I asked.", True), ("Boş ver!", True),
    ("Never mind the agenda. Please check the schedule instead.", False),
    ('My friend wrote "forget I asked".', False),
])
async def test_withdrawal_stops_at_owner_turn_boundary(memory, message, withdrawn):
    bits = await setup(memory, [] if withdrawn else [reply("Here is my response.")])
    runtime = MemoryRuntime(memory.store)
    bits.engine._memory_runtime = runtime
    await bits.engine._registry.register_skill(MemoryLookupSkill(runtime))
    try:
        room = await bits.coordinator.create(1, CreateRoom())
        result = await send(bits, room, message)
        assert result["status"] == "succeeded"
        assert len(bits.client.calls) == (0 if withdrawn else 1)
        if withdrawn:
            assert not result["calls"]
            assert result["steps"][0]["result"] in {"Understood.", "Tamam."}
    finally:
        await bits.coordinator.shutdown()


@pytest.mark.parametrize(("message", "group", "expected"), [
    ("Well I talked about it with Friday. Funny coincidence.", False, True),
    ("Friday ile bunu konuştum.", False, True),
    ("We talked about it on Friday.", False, False),
    ("I talked about it with Friday.", True, False),
])
async def test_named_private_conversation_is_scoped_and_available_on_first_call(memory, message, group, expected):
    bits = await setup(memory, [reply("The pottery workshop moved to Sunday."), reply("Understood.")])
    runtime = MemoryRuntime(memory.store)
    bits.engine._memory_runtime = runtime
    await bits.engine._registry.register_skill(MemoryLookupSkill(runtime))
    try:
        source = await bits.coordinator.create(1, CreateRoom(lead="friday"))
        await send(bits, source, "I am arranging a pottery workshop.")
        room = await bits.coordinator.create(1, CreateRoom(members=["ultron"] if group else []))
        result = await send(bits, room, message)
        assert result["status"] == "succeeded"
        prompt = bits.client.calls[-1]["system"]
        assert ("I am arranging a pottery workshop." in prompt.partition("<recent_conversation_evidence>")[2]) == expected
        if expected:
            evidence = json.loads(prompt.split("<recent_conversation_evidence>")[1].split("</recent_conversation_evidence>")[0])
            assert {item["agent_id"] for item in evidence} <= {"friday", "nightcrawler"}
            assert {item["attribution"] for item in evidence} == {"owner_statement", "assistant_statement_not_owner_assertion"}
        async with memory.sessions() as db:
            assert (await db.get(CoordinationRun, result["id"])).memory["recent_conversation"] == []
    finally:
        await bits.coordinator.shutdown()


async def test_invitee_can_answer_lead_without_spawning_a_second_exchange(memory):
    bits = await setup(memory, [
        tool({"action": "invite", "agent_id": "ultron", "message": "What is the deadline?"}),
        reply("Ultron, when is the deadline?"),
        tool({"action": "address", "agent_id": "speda", "message": "The deadline is Tuesday."}),
        reply("@speda, the deadline is Tuesday."), reply("I will use Tuesday for planning."),
    ])
    try:
        room = await bits.coordinator.create(1, CreateRoom())
        result = await send(bits, room, "Ask Ultron to check the deadline.")
        assert result["status"] == "succeeded"
        assert [step["agent_id"] for step in result["steps"]] == ["speda", "ultron", "speda"]
        assert [step["status"] for step in result["steps"]] == ["succeeded"] * 3
        assert "the deadline is Tuesday" in str(bits.client.calls[-1]["messages"])
    finally:
        await bits.coordinator.shutdown()


async def test_a_greeting_does_not_surface_old_assistant_reports(memory):
    await seed(memory, "SPEDA assistant report from the previous session", role="assistant")
    bits = await setup(memory, [reply("Hello!")])
    runtime = MemoryRuntime(memory.store)
    bits.engine._memory_runtime = runtime
    await bits.engine._registry.register_skill(MemoryLookupSkill(runtime))
    try:
        room = await bits.coordinator.create(1, CreateRoom())
        await send(bits, room, "Hey SPEDA!")
        assert "<recent_conversation_evidence>" not in bits.client.calls[0]["system"]
    finally:
        await bits.coordinator.shutdown()
