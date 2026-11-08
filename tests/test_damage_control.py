import asyncio
import json
import logging
import time
from contextlib import aclosing, asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.core.orchestrator import AgentOrchestrator
from app.models.delivery import TelegramLink, TelegramOwner
from app.schemas.llm import Block, LLMMessage, ProviderError
from app.schemas.sse import SSEEvent, SSEEventType
from app.services.damage_control import DamageControl
from app.services.llm_client import LLMClient
from app.skills.damage_control import ReportIncidentSkill


async def test_automatic_terminal_capture_and_agent_report(tmp_path, memory):
    protocol = DamageControl(tmp_path / "incidents.sqlite3")
    orchestrator = AgentOrchestrator(None, None, None)
    orchestrator.incidents = protocol
    context = memory.context(agent_id="ultron")

    async def stream(context):
        yield SSEEvent(type=SSEEventType.START, data={}, request_id=context.request_id, session_id=0)
        raise ProviderError("interrupted", "secret=not-in-the-incident")

    orchestrator._run = stream
    events = [event async for event in orchestrator.run(context)]
    assert events[-1].type == SSEEventType.ERROR
    terminal = protocol.journal.next()
    assert "secret" not in terminal["facts"]
    facts = json.loads(terminal["facts"])
    assert (facts["agent"], facts["code"], facts["ended_turn"]) == ("ultron", "interrupted", True)
    protocol.journal.transition(terminal["id"], "pending", "sent")

    async with aclosing(orchestrator.run(context)) as events:
        assert (await anext(events)).type == SSEEventType.START
    terminal = protocol.journal.next()
    assert json.loads(terminal["facts"])["event"] == "turn_interrupted"
    protocol.journal.transition(terminal["id"], "pending", "sent")

    result = await ReportIncidentSkill(protocol).execute(
        {"kind": "inconsistent_state", "impact": "continuing"}, context)
    assert protocol.journal.next()["id"] in result
    assert json.loads(protocol.journal.next()["facts"])["impact"] == "continuing"


async def test_journal_recovers_stopped_execution_and_never_resends_uncertain_send(tmp_path, memory):
    path = tmp_path / "incidents.sqlite3"
    protocol = DamageControl(path)
    active = protocol.begin(memory.context(agent_id="ratchet"))
    sending = protocol.capture({"event": "send_already_started"})
    protocol.journal.transition(sending, "pending", "sending")
    recovered = DamageControl(path)
    recovered.journal.recover()
    row = recovered.journal.next()
    assert row["id"] == active
    assert json.loads(row["facts"])["event"] == "backend_stopped_with_active_execution"
    with recovered.journal.connect() as db:
        assert db.execute("SELECT status FROM incidents WHERE id=?", (sending,)).fetchone()[0] == "unknown"


async def test_analysis_and_verified_owner_delivery(tmp_path, memory, monkeypatch):
    protocol = DamageControl(tmp_path / "incidents.sqlite3")
    async with memory.sessions() as db, db.begin():
        db.add(TelegramOwner(user_id=1, sender_id=99))
        db.add(TelegramLink(user_id=1, agent_id="speda", chat_id=99))
    telegram = AsyncMock(return_value={"message_id": 321})
    deliveries = SimpleNamespace(sessions=memory.sessions, config=SimpleNamespace(telegram_owner_id=99),
                                 telegram=telegram, token=lambda _: "test")
    monkeypatch.setattr("app.services.damage_control.render_report",
                        lambda facts, analysis: b"%PDF-1.7 " + analysis.encode())
    calls = []

    @asynccontextmanager
    async def stream_message(*, model, system, messages, role, tools, max_tokens):
        calls.append((model, role, tools, json.loads(messages[0]["content"])))
        message = LLMMessage([Block(type="text", text="Verified synthetic assessment.")], "end_turn")
        yield SimpleNamespace(get_final_message=AsyncMock(return_value=message))

    client = object.__new__(LLMClient)
    client.stream_message = stream_message
    analyse = AgentOrchestrator(None, client, None).analyse_incident
    identity = protocol.capture({"event": "test_event"})
    task = asyncio.create_task(protocol.run(analyse, deliveries))
    try:
        async with asyncio.timeout(3):
            while not telegram.called:
                await asyncio.sleep(0.01)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    model, role, tools, facts = calls[0]
    assert (model, role, tools, facts["event"]) == (None, "background", [], "test_event")
    assert telegram.await_args.args[2]["chat_id"] == 99
    assert telegram.await_args.kwargs["files"]["document"] == (
        f"damage-control-{identity}.pdf", b"%PDF-1.7 Verified synthetic assessment.", "application/pdf")
    with protocol.journal.connect() as db:
        assert tuple(db.execute("SELECT status, telegram_message_id FROM incidents").fetchone()) == ("sent", 321)


async def test_docker_replay_and_secret_bearing_log(tmp_path):
    protocol = DamageControl(tmp_path / "incidents.sqlite3")
    protocol.journal.docker_cursor()
    event = {"Action": "die", "timeNano": time.time_ns(),
             "Actor": {"ID": "test", "Attributes": {"name": "speda-postgres", "exitCode": "137"}}}
    protocol.observe_container(event)
    protocol.observe_container(event)
    protocol.handle(logging.LogRecord("app.service", logging.ERROR, __file__, 1,
                                      "request contained token=%s", ("secret-value",), None))
    with protocol.journal.connect() as db:
        facts = [json.loads(row[0]) for row in db.execute("SELECT facts FROM incidents ORDER BY timestamp")]
    assert [f["event"] for f in facts] == ["service_unexpected_exit", "runtime_log"]
    assert "secret-value" not in json.dumps(facts)
