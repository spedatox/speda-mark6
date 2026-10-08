# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unresolved observations remain unsaved without demanding repeated answers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.skills.observations import RecordObservationSkill
import app.skills.observations as observation_skill


@pytest.mark.asyncio
async def test_date_confirmation_is_returned_as_a_question_not_a_rejection(monkeypatch):
    monkeypatch.setattr(
        observation_skill,
        "resolve_evidence",
        AsyncMock(return_value=[{"ref": "message:latest", "quote": "I submitted it."}]),
    )
    monkeypatch.setattr(observation_skill, "session_review_evidence", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        observation_skill,
        "ask_json",
        AsyncMock(return_value={
            "allow": False,
            "needs_owner_confirmation": True,
            "question": "What date did you submit it?",
            "reason": "The event date is not specified.",
        }),
    )
    record = AsyncMock()
    monkeypatch.setattr(observation_skill, "record_observations", record)
    context = SimpleNamespace(db=object(), user_id=1, session_id=1, model="test", request_id="r1", agent_id="speda")

    result = await RecordObservationSkill().execute({"observations": [{
        "content": "The owner submitted the application.",
        "level": "explicit",
        "domain": "event",
        "evidence": [{"ref": "message:latest", "quote": "I submitted it."}],
    }]}, context)

    assert "needs owner confirmation" in result
    assert "What date did you submit it?" in result
    assert "Do not discard" in result
    assert "Nothing saved" in result
    assert "already clearly answered" in result
    record.assert_not_awaited()


@pytest.mark.parametrize("verdict", [
    {"allow": False, "needs_owner_confirmation": True},
    {"allow": True, "needs_owner_confirmation": True, "question": "  "},
    {"allow": False, "needs_owner_confirmation": True, "question": 123},
    {"allow": False, "reason": "Uncertain scope"},
    None,
])
async def test_missing_clarification_rejection_or_outage_never_writes(monkeypatch, verdict):
    monkeypatch.setattr(observation_skill, "resolve_evidence", AsyncMock(return_value=[
        {"ref": "message:1", "quote": "Yes."}]))
    monkeypatch.setattr(observation_skill, "session_review_evidence", AsyncMock(return_value=[]))
    review = AsyncMock(return_value=verdict) if verdict is not None else AsyncMock(side_effect=TimeoutError())
    monkeypatch.setattr(observation_skill, "ask_json", review)
    record = AsyncMock()
    monkeypatch.setattr(observation_skill, "record_observations", record)
    ctx = SimpleNamespace(db=object(), user_id=1, session_id=1, model="test", request_id="r1", agent_id="speda")
    result = await RecordObservationSkill().execute({"observations": [{
        "content": "The owner submitted the application.", "level": "explicit", "domain": "event",
        "evidence": [{"ref": "message:1", "quote": "Yes."}],
    }]}, ctx)
    record.assert_not_awaited()
    assert "Recorded" not in result
    assert "What is the exact date" not in result
