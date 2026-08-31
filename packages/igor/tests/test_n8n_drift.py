# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The shipped-workflow drift probe.

Regression, 2026-08-05: the owner's 08:00 briefing arrived as emoji headings and
a markdown table, and the health briefing announced a sync outage. Both intents
had been rewritten and committed — and neither had ever been pushed into n8n.
The live `morning_brief` was still the older text, which instructs the model to
LIST events and write a heading, so the agent produced exactly that. Nothing
anywhere reported that the repo and n8n disagreed.

The probe answers one deterministic question and returns [] when clean, because
the empty return is what stops the n8n branch without spending a turn.
"""

import json

import pytest

from app.services import n8n_drift


def _wf(name: str, js: str, node: str = "Briefing list", active: bool = True) -> dict:
    return {
        "id": "wf1", "name": name, "active": active,
        "nodes": [
            {"name": "Every 5 minutes", "type": "n8n-nodes-base.scheduleTrigger"},
            {"name": node, "type": "n8n-nodes-base.code", "parameters": {"jsCode": js}},
            {"name": "Igor: run briefing", "type": "n8n-nodes-base.httpRequest"},
        ],
    }


class _Client:
    """Stands in for N8nClient. Records whether the full-fetch path was used.

    `configured` is a PROPERTY here because it is a property on the real
    N8nClient. An earlier version of this stub made it a method, so every test
    passed while the probe raised "'bool' object is not callable" against the
    real client — a stub that does not match the interface it doubles is worse
    than no test at all.
    """

    def __init__(self, workflows, configured=True):
        self._workflows = workflows
        self._configured = configured
        self.fetched = []

    @property
    def configured(self):
        return self._configured

    async def list_workflows(self):
        return self._workflows

    async def get_workflow(self, wf_id):
        self.fetched.append(wf_id)
        return next((w for w in self._workflows if w.get("id") == wf_id), None)


@pytest.fixture
def shipped(monkeypatch, tmp_path):
    """Point the probe at a scratch scripts/n8n directory."""

    def _write(name: str, js: str, node: str = "Briefing list"):
        (tmp_path / f"{name.lower().replace(' ', '_')}.json").write_text(
            json.dumps(_wf(name, js, node)), encoding="utf-8",
        )

    monkeypatch.setattr(n8n_drift, "_SHIPPED_DIR", tmp_path)
    return _write


@pytest.mark.asyncio
async def test_in_sync_reports_nothing(shipped):
    shipped("Scheduled briefings", "const A = 1;")
    client = _Client([_wf("Scheduled briefings", "const A = 1;")])

    assert await n8n_drift.scan(client) == []


@pytest.mark.asyncio
async def test_the_actual_regression_is_caught(shipped):
    """Live code shorter than committed — exactly what happened on 08-05."""
    shipped("Scheduled briefings", "const INTENT = 'prose, no headings';" + " " * 900)
    client = _Client([_wf("Scheduled briefings", "const INTENT = 'listele';")])

    drift = await n8n_drift.scan(client)

    assert len(drift) == 1
    assert drift[0]["reason"] == "code_drift"
    assert drift[0]["workflow"] == "Scheduled briefings"
    assert drift[0]["live_chars"] < drift[0]["repo_chars"]


@pytest.mark.asyncio
async def test_whitespace_only_difference_is_not_drift(shipped):
    """A trailing newline from the n8n editor must not page anyone."""
    shipped("Scheduled briefings", "const A = 1;")
    client = _Client([_wf("Scheduled briefings", "const A = 1;\n\n")])

    assert await n8n_drift.scan(client) == []


@pytest.mark.asyncio
async def test_a_shipped_workflow_absent_from_n8n_is_drift(shipped):
    shipped("Mail watch", "const A = 1;")
    client = _Client([])

    drift = await n8n_drift.scan(client)
    assert [d["reason"] for d in drift] == ["missing"]


@pytest.mark.asyncio
async def test_a_switched_off_workflow_is_drift(shipped):
    """Deactivation is the other silent failure — n8n's PUT can cause it."""
    shipped("Scheduled briefings", "const A = 1;")
    client = _Client([_wf("Scheduled briefings", "const A = 1;", active=False)])

    drift = await n8n_drift.scan(client)
    assert [d["reason"] for d in drift] == ["inactive"]


@pytest.mark.asyncio
async def test_a_renamed_code_node_is_drift(shipped):
    shipped("Scheduled briefings", "const A = 1;", node="Briefing list")
    client = _Client([_wf("Scheduled briefings", "const A = 1;", node="Renamed")])

    drift = await n8n_drift.scan(client)
    assert [d["reason"] for d in drift] == ["node_missing"]


@pytest.mark.asyncio
async def test_nodes_are_fetched_when_the_listing_omits_them(shipped):
    shipped("Scheduled briefings", "const A = 1;")
    listing = _wf("Scheduled briefings", "const A = 1;")
    full = json.loads(json.dumps(listing))
    listing.pop("nodes")

    client = _Client([listing])
    client._workflows = [listing]
    client.get_workflow = lambda wf_id: _async(full)  # type: ignore[assignment]

    assert await n8n_drift.scan(client) == []


async def _async(value):
    return value


# ── The probe must never break the poller ───────────────────────────────────


@pytest.mark.asyncio
async def test_an_unconfigured_client_returns_empty(shipped):
    shipped("Scheduled briefings", "const A = 1;")
    assert await n8n_drift.scan(_Client([], configured=False)) == []


@pytest.mark.asyncio
async def test_an_n8n_outage_returns_empty_rather_than_raising(shipped):
    shipped("Scheduled briefings", "const A = 1;")

    class _Dead(_Client):
        async def list_workflows(self):
            raise RuntimeError("connection refused")

    assert await n8n_drift.scan(_Dead([])) == []


@pytest.mark.asyncio
async def test_an_unreadable_template_is_skipped(monkeypatch, tmp_path):
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(n8n_drift, "_SHIPPED_DIR", tmp_path)

    assert await n8n_drift.scan(_Client([])) == []


@pytest.mark.asyncio
async def test_no_templates_means_nothing_to_check(monkeypatch, tmp_path):
    monkeypatch.setattr(n8n_drift, "_SHIPPED_DIR", tmp_path / "nope")
    assert await n8n_drift.scan(_Client([])) == []


def test_the_stub_matches_the_real_client_interface():
    """Guards the bug above: `configured` must be the same KIND of attribute on
    the stub as on N8nClient, or these tests validate a fiction."""
    from app.services.n8n_api import N8nClient

    real = type(N8nClient).__mro__ and N8nClient
    assert isinstance(getattr(real, "configured"), property), (
        "N8nClient.configured stopped being a property — n8n_drift.scan reads it "
        "as one"
    )
    assert isinstance(getattr(_Client, "configured"), property), (
        "the stub drifted from the real client"
    )
