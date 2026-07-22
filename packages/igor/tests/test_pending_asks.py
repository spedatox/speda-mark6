"""The permission relay between an external peer's safety gate and the owner.

Igor decides nothing here — it stores, forwards and correlates. What these
assert is that absence never becomes approval: an expired ask, a disconnected
agent and an unknown id all fail closed, and the peer's own timeout is always
the one that fires first.
"""

import asyncio
import time

import pytest

from app.services.pending_asks import PendingAsk, PendingAsks

FORCE_PUSH = "git push --force origin main"


class FakeWS:
    """Stands in for WebSocketManager."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    async def send(self, agent_id: str, message: dict) -> None:
        self.sent.append((agent_id, message))


def _frame(ask_id: str = "a1", **over) -> dict:
    frame = {
        "type": "permission_request",
        "ask_id": ask_id,
        "job_id": "job-1",
        "tool": "run_command",
        "action_key": FORCE_PUSH,
        "reason": "stopped by the safety gate: command matches a high-blast-radius pattern",
        "timeout_s": 120,
    }
    frame.update(over)
    return frame


@pytest.fixture
def asks():
    return PendingAsks(FakeWS())


# ── Ingress ──────────────────────────────────────────────────────────────────
def test_an_ask_is_recorded_and_listed(asks):
    ask = asks.record("optimus", _frame())
    assert ask is not None
    [open_ask] = asks.list_open()
    assert open_ask.ask_id == "a1"
    assert open_ask.action_key == FORCE_PUSH


def test_the_exact_action_is_preserved_verbatim(asks):
    """An owner approving a force-push needs to see which branch."""
    ask = asks.record("optimus", _frame(action_key="git push --force origin production"))
    assert ask.to_dict()["action_key"] == "git push --force origin production"


@pytest.mark.parametrize("bad", [{"ask_id": ""}, {"tool": ""}])
def test_an_unanswerable_frame_is_dropped(asks, bad):
    """An ask with no id can never be answered, so storing it would only leak a
    row into the tray that no click could clear."""
    assert asks.record("optimus", _frame(**bad)) is None
    assert asks.list_open() == []


def test_the_peer_timeout_outlives_igors_record(asks):
    """The peer must always give up first. If Igor expired first it would drop
    the record while the peer was still waiting, and a late approval would
    arrive with nowhere to go."""
    ask = asks.record("optimus", _frame(timeout_s=120))
    assert ask.seconds_left > 120


def test_a_missing_timeout_falls_back_to_the_default(asks):
    ask = asks.record("optimus", _frame(timeout_s=None))
    assert ask.seconds_left > 0


def test_a_nonsense_timeout_falls_back_rather_than_raising(asks):
    ask = asks.record("optimus", _frame(timeout_s="soon"))
    assert ask is not None and ask.seconds_left > 0


# ── Egress ───────────────────────────────────────────────────────────────────
def test_answering_sends_the_decision_to_the_right_peer(asks):
    asks.record("optimus", _frame())
    assert asyncio.run(asks.answer("a1", approved=True, remember=True))

    [(agent_id, frame)] = asks._ws.sent
    assert agent_id == "optimus"
    assert frame == {"type": "permission_response", "ask_id": "a1",
                     "approved": True, "remember": True, "note": ""}


def test_a_denial_carries_the_owners_note(asks):
    asks.record("optimus", _frame())
    asyncio.run(asks.answer("a1", approved=False, note="not on main"))
    assert asks._ws.sent[0][1]["note"] == "not on main"


def test_an_answered_ask_leaves_the_tray(asks):
    asks.record("optimus", _frame())
    asyncio.run(asks.answer("a1", approved=True))
    assert asks.list_open() == []


def test_answering_twice_sends_one_decision(asks):
    """A double-click must not send two answers: the peer resolves the first and
    logs the second as unmatched, which makes the logs lie about what the owner
    actually did."""
    asks.record("optimus", _frame())

    async def scenario():
        return await asyncio.gather(
            asks.answer("a1", approved=True),
            asks.answer("a1", approved=False),
        )

    results = asyncio.run(scenario())
    assert sorted(results) == [False, True]
    assert len(asks._ws.sent) == 1


def test_answering_an_unknown_id_reports_failure(asks):
    assert asyncio.run(asks.answer("never-existed", approved=True)) is False
    assert asks._ws.sent == []


# ── Absence is never approval ────────────────────────────────────────────────
def test_an_expired_ask_disappears_and_cannot_be_approved(asks):
    ask = asks.record("optimus", _frame())
    ask.expires_at = time.time() - 1

    assert asks.list_open() == []
    assert asyncio.run(asks.answer("a1", approved=True)) is False
    assert asks._ws.sent == [], "an expired ask must not be approvable"


def test_expiry_sends_nothing_to_the_peer(asks):
    """The peer ran its own timeout and already denied. A late denial would be
    answering a question that is no longer being asked."""
    ask = asks.record("optimus", _frame())
    ask.expires_at = time.time() - 1
    assert asks.sweep() == 1
    assert asks._ws.sent == []


def test_a_disconnected_agents_asks_are_forgotten(asks):
    asks.record("optimus", _frame("a1"))
    asks.record("optimus", _frame("a2"))
    asks.record("centurion", _frame("a3"))

    assert asks.drop_agent("optimus") == 2
    assert [a.ask_id for a in asks.list_open()] == ["a3"]
    assert asyncio.run(asks.answer("a1", approved=True)) is False


# ── Ordering ─────────────────────────────────────────────────────────────────
def test_the_tray_shows_oldest_first(asks):
    for i in range(3):
        ask = asks.record("optimus", _frame(f"a{i}"))
        ask.created_at = 1000 + i
    assert [a.ask_id for a in asks.list_open()] == ["a0", "a1", "a2"]


def test_a_chat_ask_carries_its_stream_id(asks):
    """Chat jobs render inline; dispatched jobs have no stream and are polled."""
    with_chat = asks.record("optimus", _frame("a1", chat_id="c9"))
    without = asks.record("optimus", _frame("a2"))
    assert with_chat.chat_id == "c9"
    assert without.chat_id is None
