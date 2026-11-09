import asyncio
from types import SimpleNamespace

import httpx

from app.config import settings
from app.main import create_app
from app.services.damage_control import IncidentJournal


async def test_owner_journal_groups_repeats_and_assessment_does_not_release_delivery(tmp_path, monkeypatch):
    journal = IncidentJournal(tmp_path / "journal.sqlite3")
    facts = {"event": "turn_error", "agent": "ultron", "ended_turn": True, "code": "deadline"}
    older = journal.add(facts, "held_pending")
    identity = journal.add(facts, "held_pending")
    journal.add({"event": "synthetic_protocol_test"}, "sent")
    journal.add({"event": "active_turn"}, "active")

    entered = asyncio.Event()
    release = asyncio.Event()

    async def analyse(observed):
        assert observed["incident_id"] == identity
        entered.set()
        await release.wait()
        return "The turn reached its deadline."

    app = create_app()
    app.state.damage_control_journal = journal
    app.state.orchestrator = SimpleNamespace(analyse_incident=analyse)
    monkeypatch.setattr("app.services.incident_journal.render_report",
                        lambda recorded, text: b"%PDF-1.7 " + text.encode())
    exported_ids = []
    def bulk(records, *, bulk):
        assert bulk
        exported_ids[:] = [facts["incident_id"] for facts, _ in records]
        return b"%PDF-1.7 bulk"
    monkeypatch.setattr("app.services.incident_journal.render_reports", bulk)
    root = "/iacon/hall-of-records/incidents"
    endpoint = f"{root}/{identity}"

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get(root)).status_code == 401
        assert (await client.post(f"{root}/export", json={})).status_code == 401
        client.headers["X-API-Key"] = settings.speda_api_key
        listing = (await client.get(root, params={"q": "ultron", "severity": "critical"})).json()
        row, = listing["incidents"]
        assert (row["id"], row["occurrences"], row["severity"]) == (identity, 2, "critical")
        assert len((await client.get(root)).json()["incidents"]) == 1
        assert len((await client.get(root, params={"reviewed": True})).json()["incidents"]) == 2
        assert (await client.get(root, params={"q": older})).json()["incidents"][0]["id"] == older
        detail = (await client.get(endpoint)).json()
        assert [item["id"] for item in detail["recent_occurrences"]] == [identity, older]
        assert "ended" in detail["facts"]["summary"]
        all_matching = await client.post(f"{root}/export", json={"q": "ultron", "severity": "critical"})
        assert all_matching.content == b"%PDF-1.7 bulk"
        assert exported_ids == [identity, older]
        selected = await client.post(f"{root}/export", json={"ids": [older]})
        assert selected.headers["content-disposition"].endswith('"damage-control-journal.pdf"')
        assert exported_ids == [older]
        assert (await client.post(f"{root}/export", json={"ids": []})).status_code == 422

        first = asyncio.create_task(client.post(f"{endpoint}/assessment"))
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            assert (await client.post(f"{endpoint}/assessment")).json()["assessment_state"] == "running"
        finally:
            release.set()
        assessed = (await first).json()
        assert assessed["assessment"] == "The turn reached its deadline."
        assert assessed["delivery_state"] == "held_pending"
        export = await client.get(f"{endpoint}/pdf")
        assert export.headers["content-type"] == "application/pdf"
        assert export.content == b"%PDF-1.7 The turn reached its deadline."

        assert (await client.put(f"{endpoint}/review")).json()["reviewed"]
        assert not (await client.get(root)).json()["incidents"]
        repeat = journal.add(facts, "held_pending")
        assert (await client.get(root)).json()["incidents"][0]["id"] == repeat
