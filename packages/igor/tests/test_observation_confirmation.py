# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""An incomplete date pauses for the owner; it must not silently lose a fact."""

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
    record.assert_not_awaited()
