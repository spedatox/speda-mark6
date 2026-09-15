from types import SimpleNamespace

import pytest

from app.core.registry import CapabilityRegistry
from app.services.relevant_recall import (
    derived_recall_query,
    facts_for_message,
    salient_evidence,
)


def test_briefing_calendar_evidence_changes_recall_query_generically():
    objective = "Brief me for tomorrow."
    result = {
        "events": [{
            "title": "Supplier Review",
            "location": "Istanbul",
            "start": "2026-09-16T09:00:00+03:00",
            "attendees": [{"name": "Deniz Kaya"}],
        }]
    }

    evidence = salient_evidence(result, known_text=objective + " Bursa")
    query = derived_recall_query(objective, evidence)

    assert "Istanbul" in evidence
    assert "Supplier Review" in evidence
    assert "Istanbul" in query
    assert "Bursa" not in query
    assert len(evidence) <= 8


def test_plain_text_new_entity_and_date_are_salient():
    evidence = salient_evidence(
        "The Acme Project review with Ada Lovelace is at 2026-09-17 14:30.",
        known_text="What matters this week?",
    )

    assert "2026-09-17" in evidence
    assert "14:30" in evidence
    assert any("Ada Lovelace" in item for item in evidence)


def test_email_state_change_keeps_subject_and_change_together():
    evidence = salient_evidence(
        "Inbox update: The supplier meeting moved online. Regards, Operations Team.",
        known_text="Check my inbox",
    )
    assert any("meeting moved online" in item.lower() for item in evidence)


def test_structured_deadline_extension_keeps_task_relationship():
    evidence = salient_evidence({"task": "Launch", "deadline": "extended to Friday"})
    assert "Launch -> deadline extended to Friday" in evidence


def test_application_approval_keeps_subject_and_state_together():
    evidence = salient_evidence({"title": "Visa application", "status": "approved"})
    assert "Visa application -> status approved" in evidence


def test_meaningful_state_change_survives_irrelevant_strings():
    noise = "\n".join(f"Decorative Heading {i}" for i in range(40))
    evidence = salient_evidence(noise + "\nThe planning meeting moved online.\nFooter Text")
    assert any("meeting moved online" in item.lower() for item in evidence)


def test_large_output_remains_a_small_frontier_not_a_summary():
    result = {
        "items": [
            {"title": f"Task {i}", "status": "blocked" if i % 2 else "completed"}
            for i in range(200)
        ]
    }
    evidence = salient_evidence(result)
    assert len(evidence) <= 8
    assert all(len(item) <= 120 for item in evidence)
    assert len(" | ".join(evidence)) < 1000


@pytest.mark.parametrize("result", ["", "[]", "{}", "(no output)", {"count": 0}])
def test_no_material_evidence_does_not_create_a_frontier(result):
    assert salient_evidence(result, known_text="Brief me for tomorrow") == []


def test_existing_evidence_is_not_reintroduced():
    result = {"location": "Istanbul", "date": "2026-09-16"}
    assert salient_evidence(
        result, known_text="Tomorrow in Istanbul is 2026-09-16"
    ) == []


async def test_existing_direct_recall_still_uses_latest_user_message(monkeypatch):
    seen = []

    async def fake_search(_db, *, user_id, query, limit):
        seen.append((user_id, query, limit))
        return []

    monkeypatch.setattr("app.services.observations.search_observations", fake_search)
    history = [{"role": "user", "content": "[2026-09-15 10:00 TRT] What did I decide about Atlas?"}]

    assert await facts_for_message(7, object(), history, request_id="r1") == ""
    assert seen and seen[0][1] == "What did I decide about Atlas?"


async def test_annotated_mcp_read_is_memoized_and_changed_scope_executes():
    class FakeMCP:
        server_name = "calendar"

        def __init__(self):
            self.calls = []

        async def connect(self):
            return None

        async def list_tools(self):
            return [{
                "name": "read_range",
                "description": "Read a calendar range.",
                "input_schema": {"type": "object"},
                "annotations": {"readOnlyHint": True},
            }]

        async def call_tool(self, name, args):
            self.calls.append((name, dict(args)))
            return f"result-{len(self.calls)}"

    client = FakeMCP()
    registry = CapabilityRegistry()
    await registry.register_mcp(client)
    context = SimpleNamespace(extra={}, request_id="turn", agent_id="speda")

    assert await registry.execute("read_range", {"day": "today"}, context) == "result-1"
    assert await registry.execute("read_range", {"day": "today"}, context) == "result-1"
    assert await registry.execute("read_range", {"day": "tomorrow"}, context) == "result-2"
    assert len(client.calls) == 2
    assert registry.call_is_read_only("read_range", {"day": "today"}) is True


async def test_unannotated_mcp_call_is_never_suppressed():
    class FakeMCP:
        server_name = "mixed"

        def __init__(self):
            self.calls = 0

        async def connect(self):
            return None

        async def list_tools(self):
            return [{"name": "do_thing", "description": "Mixed operation.", "input_schema": {}}]

        async def call_tool(self, name, args):
            self.calls += 1
            return str(self.calls)

    client = FakeMCP()
    registry = CapabilityRegistry()
    await registry.register_mcp(client)
    context = SimpleNamespace(extra={}, request_id="turn", agent_id="speda")

    await registry.execute("do_thing", {}, context)
    await registry.execute("do_thing", {}, context)
    assert client.calls == 2
    assert registry.call_is_read_only("do_thing", {}) is False
