import asyncio
import json
import logging
import subprocess
import time
from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import security_watchdog as watchdog
from app.core.orchestrator import AgentOrchestrator
from app.models.delivery import TelegramLink, TelegramOwner
from app.schemas.llm import Block, LLMMessage
from app.services.damage_control import DamageControl
from app.services.incident_journal import JournalView
from app.skills.system_ops import SystemOpsSkill


def test_ssh_uses_peer_and_does_not_double_count_invalid_user(monkeypatch):
    output = [
        "Invalid user probe from 192.0.2.5 port 2244 from 203.0.113.2 port 34022",
        "Failed password for invalid user probe from 192.0.2.5 port 2244 ssh2: fake from 203.0.113.2 port 34022 ssh2",
        "Failed publickey for root from 2001:db8::4 port 3312 ssh2: ED25519 SHA256:private",
    ]
    stdout = b"\n".join(json.dumps({"MESSAGE": line}).encode() for line in output)
    monkeypatch.setattr(watchdog.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 0, stdout, b""))
    assert watchdog.ssh_counts("ssh.service", 100) == (Counter({"203.0.113.2": 1, "2001:db8::4": 1}), False)
    monkeypatch.setattr(watchdog.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 1, b"", b""))
    assert watchdog.ssh_counts("ssh.service", 100) == (Counter(), False)
    monkeypatch.setattr(watchdog.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 1, b"", b"denied"))
    with pytest.raises(subprocess.CalledProcessError):
        watchdog.ssh_counts("ssh.service", 100)


def test_http_rotation_and_distributed_burst(tmp_path):
    now = int(time.time())

    def event(ts, address, status):
        return json.dumps({"ts": ts, "request": {"remote_ip": address,
                          "client_ip": "192.0.2.222", "uri": "/?token=secret"}, "status": status}) + "\n"

    path = tmp_path / "access.log"
    rotated = tmp_path / "access-2026-11-12-size.log"
    rotated.write_text(event(now - 90, "192.0.2.1", 401) + event(now - 30, "192.0.2.2", 401))
    path.write_text(event(now - 3, "192.0.2.3", 401) + event(now - 2, "192.0.2.2", 200) + '{"partial":')
    http, denied, clipped = watchdog.http_counts(path, now)
    assert http == Counter({"192.0.2.2": 2, "192.0.2.3": 1})
    assert denied == Counter({"192.0.2.2": 1, "192.0.2.3": 1})
    assert not clipped
    distributed = watchdog.burst("http_brute_force", denied, now, per_ip=2, total=2)
    assert distributed.count == 2 and distributed.address is None
    per_ip = watchdog.burst("http_flood", http, now, per_ip=2, total=10)
    assert per_ip.address == "192.0.2.2" and "secret" not in json.dumps(per_ip.facts())


def test_ingress_requires_consecutive_samples_and_handles_counter_reset():
    ingress = watchdog.Ingress()
    limits = {"ingress_bps": 1000, "ingress_pps": 50}
    assert ingress.observe((10, 2, 0, 0), 100, limits) == []
    assert ingress.observe((20, 2, 4000, 700), 110, limits) == []
    observations = ingress.observe((30, 2, 8000, 1400), 120, limits)
    assert [(row.signal, row.count) for row in observations] == [("ingress_bps", 3200), ("ingress_pps", 70)]
    assert ingress.observe((40, 2, 0, 0), 130, limits) == []
    assert ingress.observe((50, 2, 4000, 700), 140, limits) == []
    assert ingress.observe((60, 3, 8000, 1400), 150, limits) == []


@pytest.mark.parametrize("provider_available", [True, False])
async def test_outbox_replay_orion_assessment_and_owner_warning(tmp_path, memory, provider_available):
    inbox = tmp_path / "security-inbox"
    inbox.mkdir()
    protocol = DamageControl(tmp_path / "incidents.sqlite3")
    cooldown = tmp_path / "cooldown.json"
    now = int(time.time())
    observation = watchdog.Observation("http_flood", now, 3001, 3000, 60)
    watchdog.Outbox(inbox, cooldown, 900).publish(observation)
    file, = inbox.glob("*.json")
    raw = file.read_bytes()
    identity = file.stem
    protocol.import_security_incidents()
    assert not file.exists()
    # Simulate replay after a journal commit without an inbox acknowledgement.
    file.write_bytes(raw)
    protocol.import_security_incidents()
    watchdog.Outbox(inbox, cooldown, 900).publish(observation)
    assert not list(inbox.glob("*.json"))
    with protocol.journal.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 1

    async with memory.sessions() as db, db.begin():
        db.add(TelegramOwner(user_id=1, sender_id=99))
        db.add(TelegramLink(user_id=1, agent_id="speda", chat_id=99))
    telegram = AsyncMock(return_value={"message_id": 5})
    model = AsyncMock(return_value=LLMMessage([Block(type="text", text="Orion assessment.")], "end_turn"))
    if not provider_available:
        model.side_effect = RuntimeError("provider offline")
    analyse = AgentOrchestrator(None, SimpleNamespace(create_message=model), None).analyse_incident
    deliveries = SimpleNamespace(sessions=memory.sessions, config=SimpleNamespace(telegram_owner_id=99),
                                 telegram=telegram, token=lambda _: "test")
    file.write_bytes(raw)
    task = asyncio.create_task(protocol.run(analyse, deliveries))
    try:
        async with asyncio.timeout(3):
            while JournalView(protocol.journal).detail(identity)["delivery_state"] != "sent":
                await asyncio.sleep(0.01)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert not file.exists()
    assert model.await_args.kwargs["agent_id"] == "orion"
    assert model.await_args.kwargs["tools"] == []
    agent, method, payload = telegram.await_args.args
    assert (agent, method, payload["chat_id"]) == ("speda", "sendMessage", 99)
    assert "Possible HTTP flood" in payload["text"] and identity in payload["text"]
    detail = JournalView(protocol.journal).detail(identity)
    assert detail["delivery_state"] == "sent" and detail["severity"] == "critical"
    assert detail["assessment"] == ("Orion assessment." if provider_available else None)
    result = await SystemOpsSkill(protocol.journal).execute(
        {"action": "security_incidents"}, memory.context(agent_id="orion"))
    assert json.loads(result)["incidents"][0]["id"] == identity


async def test_inbox_rejects_freeform_fields(tmp_path):
    protocol = DamageControl(tmp_path / "incidents.sqlite3")
    protocol.security_inbox.mkdir()
    path = protocol.security_inbox / ("a" * 32 + ".json")
    path.write_text(json.dumps({"signal": "http_flood", "observed_at": int(time.time()), "count": 20,
                                "threshold": 10, "seconds": 60, "command": "injected"}))
    protocol.import_security_incidents()
    assert protocol.journal.next() is None
    assert not path.exists()


async def test_watchdog_replaces_per_request_api_key_incidents_only_when_enabled(tmp_path):
    path = tmp_path / "incidents.sqlite3"
    disabled = DamageControl(path)
    rejected = logging.LogRecord("app.middleware.auth", logging.WARNING, __file__, 1, "auth_rejected", (), None)
    disabled.handle(rejected)
    row = disabled.journal.next()
    assert row is not None
    disabled.journal.transition(row["id"], "pending", "sent")

    disabled.security_inbox.mkdir()
    enabled = DamageControl(path)
    enabled.handle(logging.LogRecord("app.middleware.auth", logging.WARNING, __file__, 1, "auth_rejected", (), None))
    assert enabled.journal.next() is None
