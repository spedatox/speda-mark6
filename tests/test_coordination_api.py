import json

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from app.config import settings
from app.main import create_app
from app.models.memory_serving import MemoryChange
from app.schemas.coordination import CreateRoom, Decision
from app.schemas.llm import Block, LLMMessage
from app.services.coordination_budget import CoordinationError
from app.skills.collaboration import DecisionSkill
from tests.test_coordination import setup, reply, send


async def test_disabling_room_ui_never_restores_superseded_profile_engine(memory, monkeypatch):
    from app import database
    monkeypatch.setattr(database, "engine", memory.engine)
    monkeypatch.setattr(database, "AsyncSessionLocal", memory.sessions)
    monkeypatch.setattr(settings, 'orchestration_profiles_enabled', False)
    monkeypatch.setattr(settings, 'orchestration_rooms_enabled', False)
    app = create_app()
    async with app.router.lifespan_context(app):
        assert app.state.orchestrator._profiles is app.state.profiles
        assert app.state.client_compat.coordinator is app.state.coordinator
        assert app.state.iacon is app.state.orion.catalogue


async def test_shell_has_no_private_data_api_auth_and_paused_gate(memory, monkeypatch):
    from app import database
    monkeypatch.setattr(database, "engine", memory.engine)
    monkeypatch.setattr(database, "AsyncSessionLocal", memory.sessions)
    app = create_app()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            shell = await http.get("/rooms-ui")
            assert shell.status_code == 200 and settings.speda_api_key not in shell.text
            assert (await http.get("/coordination/rooms")).status_code == 401
            headers = {"X-API-Key": settings.speda_api_key}
            response = await http.post("/coordination/rooms", headers=headers, json={"lead": "unknown"})
            assert response.status_code == 422
            room = (await http.post("/coordination/rooms", headers=headers, json={"lead": "nightcrawler"})).json()
            assert room["lead"] == "friday"
            denied = await http.post(f'/coordination/rooms/{room["id"]}/messages', headers=headers,
                json={"input_id": "one", "message": "hello"})
            assert denied.status_code == 409 and denied.json()["code"] == "budget_paused"
            assert (await http.get("/coordination/budget", headers=headers)).json()["reserved"] == []
            await http.post(f'/coordination/rooms/{room["id"]}/decisions', headers=headers,
                json={"proposal": "Use isolated workspaces", "disposition": "accepted"})
            decisions = (await http.get(f'/coordination/rooms/{room["id"]}/decisions', headers=headers)).json()
            assert decisions[0]["speaker"] == "owner" and decisions[0]["disposition"] == "accepted"


async def test_room_decisions_are_scoped_versioned_and_not_owner_memory(memory):
    bits = await setup(memory, [reply("Should we use isolated workspaces?"), reply("Understood")])
    room = await bits.coordinator.create(1, CreateRoom())
    result = await send(bits, room)
    events = (await bits.coordinator.events(1, room["id"]))["events"]
    delivered = next(e for e in events if e["kind"] == "message")
    decision = await bits.coordinator.record_decision(1, room["id"], Decision(
        proposal="Use isolated workspaces", evidence=[delivered["sequence"]], disposition="accepted"))
    with pytest.raises(CoordinationError):
        await bits.coordinator.record_decision(1, room["id"], Decision(id=decision["id"],
            expected_version=0, proposal="Stale overwrite"))
    with pytest.raises(CoordinationError):
        await bits.coordinator.record_decision(1, room["id"], Decision(proposal="Invented evidence", evidence=[99999]))
    await send(bits, room, "Yes, use them", input_id="two")
    assert "recorded_room_decisions" in str(bits.client.calls[-1]["messages"])
    async with memory.sessions() as db:
        originals = (await db.execute(select(MemoryChange).order_by(MemoryChange.id))).scalars().all()
        assert len(originals) == 2
        assert originals[-1].content == "Yes, use them"
        assert originals[-1].provenance["prior_context"]["speaker"] == "speda"
        assert originals[-1].provenance["prior_context"]["authority"] == "context_only_not_owner_evidence"
    await bits.coordinator.shutdown()


@pytest.mark.parametrize(("overrides", "code"), [
    ({}, "owner_decision"),
    ({"expected_version": 0}, "decision_version"),
    ({"evidence": [99999]}, "decision_evidence"),
])
async def test_agent_decision_records_recommendation_and_keeps_turn_running(memory, overrides, code):
    proposal = "Use isolated workspaces"
    call = Block(type="tool_use", id="recommend", name="record_decision",
                 input={"proposal": proposal, "disposition": "accepted"})
    bits = await setup(memory, [])
    await bits.engine._registry.register_skill(DecisionSkill())
    try:
        room = await bits.coordinator.create(1, CreateRoom())
        accepted = await bits.coordinator.record_decision(1, room["id"], Decision(
            proposal="Require code review", disposition="accepted"))
        overwrite = Block(type="tool_use", id="overwrite", name="record_decision", input={
            "id": accepted["id"], "expected_version": accepted["version"],
            "proposal": "Skip code review", "disposition": "accepted", **overrides,
        })
        answer = "I recommend isolated workspaces. The code review decision stands."
        bits.client.script.extend([LLMMessage([call, overwrite], "tool_use"), reply(answer)])

        result = await send(bits, room)
        assert result["status"] == "succeeded"
        tool_results = {block["tool_use_id"]: json.loads(block["content"])
                        for message in bits.client.calls[-1]["messages"]
                        if isinstance(message["content"], list)
                        for block in message["content"] if block["type"] == "tool_result"}
        recommendation = tool_results["recommend"]
        assert recommendation["disposition"] == "recommended" and recommendation["speaker"] == "speda"
        assert recommendation["proposal"] == proposal
        assert tool_results["overwrite"]["status"] == "denied"
        assert tool_results["overwrite"]["code"] == code
        decisions = await bits.coordinator.decisions(1, room["id"])
        assert sorted(decisions, key=lambda row: row["speaker"]) == [accepted, recommendation]
        events = (await bits.coordinator.events(1, room["id"]))["events"]
        assert any(event["kind"] == "message" and event["payload"]["text"] == answer for event in events)
    finally:
        await bits.coordinator.shutdown()
