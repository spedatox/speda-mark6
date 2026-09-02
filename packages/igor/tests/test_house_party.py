# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The House Party Protocol's argument handling and its authorization gate.

The regression these cover, observed in prod on 2026-08-04: every agent was
pinned to a non-Anthropic provider, and non-Anthropic providers do not enforce a
tool schema's `required`. Speda emitted `house_party({})` on Gemini and
`house_party({"action": "engage"})` on DeepSeek for turns where the owner had
plainly said "engage house party protocol". The skill read the absent `engaged`
as False, STOOD THE PROTOCOL DOWN, and handed the model a stand-down
confirmation — which the model reported to the owner as "House Party Protocol
engaged. All six agents standing by." The flag was false the whole time.

So the rule under test is: an unstated direction moves nothing and says so.
Engaging still never happens here — it only ever opens the owner's
authorization window (or validates a passphrase the owner spoke).
"""

import pytest

from app.core.context import AgentContext
from app.skills.dispatch import HousePartySkill, _read_engaged


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Point runtime_state at a scratch file so tests never touch real state."""
    from app.core import runtime_state

    monkeypatch.setattr(runtime_state, "_STATE_FILE", tmp_path / "runtime_state.json")
    monkeypatch.setattr(runtime_state, "_cache", None)
    monkeypatch.setattr(runtime_state, "_DATA_DIR", tmp_path)
    yield
    monkeypatch.setattr(runtime_state, "_cache", None)


def _context(**overrides) -> AgentContext:
    base = dict(
        agent_id="speda",
        user_id=1,
        session_id=1,
        request_id="req-test",
        triggered_by="user",
        trigger_payload={},
        output_mode="respond",
        model="test-model",
        system_prompt="",
        conversation_history=[],
        db=None,
        timezone="Europe/Istanbul",
    )
    base.update(overrides)
    return AgentContext(**base)


# ── Argument reading ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "args",
    [
        {"engaged": True},
        {"engaged": "true"},
        {"engaged": "yes"},
        {"engage": "engage"},
        {"action": "engage"},        # DeepSeek's actual prod shape
        {"state": "activate"},
        {"status": "ON"},
        {"engaged": 1},
    ],
)
def test_engage_intent_is_understood(args):
    assert _read_engaged(args) is True


@pytest.mark.parametrize(
    "args",
    [
        {"engaged": False},
        {"engaged": "false"},
        {"engaged": "no"},
        {"action": "stand_down"},
        {"action": "stand down"},
        {"state": "disengage"},
        {"engaged": 0},
    ],
)
def test_stand_down_intent_is_understood(args):
    assert _read_engaged(args) is False


@pytest.mark.parametrize(
    "args",
    [
        {},                              # Gemini's actual prod shape
        {"objective": "assemble"},       # objective only, no direction
        {"action": "whatever"},
        {"engaged": None},
        {"engaged": ""},
        None,
    ],
)
def test_absent_intent_is_none_not_false(args):
    """The whole bug in one assertion: missing != stand down."""
    assert _read_engaged(args) is None


# ── The skill's behaviour ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_args_do_not_stand_down_an_engaged_protocol():
    """`house_party({})` must leave an ENGAGED protocol engaged."""
    from app.core.runtime_state import get_house_party, set_house_party

    set_house_party(True)
    result = await HousePartySkill().execute({}, _context())

    assert get_house_party() is True, "an argument-less call silently disarmed the protocol"
    assert "REFUSED" in result
    assert "engaged=true" in result


@pytest.mark.asyncio
async def test_action_engage_does_not_stand_down():
    """DeepSeek's `{"action": "engage"}` must not read as a stand-down."""
    from app.core.runtime_state import get_house_party, set_house_party

    set_house_party(True)
    result = await HousePartySkill().execute({"action": "engage"}, _context())

    # Already engaged, so this asks for authorization again rather than
    # re-engaging — the one thing it must never do is turn the flag off.
    assert get_house_party() is True
    assert "stood down" not in result.lower()


@pytest.mark.asyncio
async def test_stand_down_when_already_down_is_reported_as_no_change():
    """The message a model confabulated 'engaged' out of. It must be unambiguous."""
    from app.core.runtime_state import get_house_party, set_house_party

    set_house_party(False)
    result = await HousePartySkill().execute({"engaged": False}, _context())

    assert get_house_party() is False
    assert "already stood down" in result.lower()
    assert "not engaged" in result.lower()


@pytest.mark.asyncio
async def test_explicit_stand_down_still_works():
    from app.core.runtime_state import get_house_party, set_house_party

    set_house_party(True)
    result = await HousePartySkill().execute({"engaged": False}, _context())

    assert get_house_party() is False
    assert "stood down" in result.lower()


# ── The authorization gate ─────────────────────────────────────────────────
#
# Engaging is desktop-only, and that check runs BEFORE the passphrase — the war
# room is built by the desktop client and nowhere else, so a phone request must
# never even open the authorization window. Every test below that expects to
# reach the passphrase logic therefore has to say which client it is speaking
# from; one that does not is testing the surface gate instead, which is what
# these three were accidentally doing.


def _desktop(**overrides) -> AgentContext:
    return _context(extra={"client_platform": "desktop"}, **overrides)


@pytest.mark.asyncio
async def test_engaging_from_a_client_that_is_not_the_desktop_is_refused():
    """A surface with no war room to show refuses rather than running invisibly."""
    from app.core.runtime_state import get_house_party, set_house_party

    for platform in (None, "android", "telegram"):
        set_house_party(False)
        context = _context(extra={"client_platform": platform} if platform else {})
        result = await HousePartySkill().execute({"engaged": True}, context)

        assert get_house_party() is False
        assert "REFUSED" in result
        assert "desktop" in result.lower()
        # The window must not open either — it is the desktop's window.
        assert "house_party_auth" not in context.extra


@pytest.mark.asyncio
async def test_engage_without_passphrase_opens_the_auth_window():
    """Engaging never flips the flag here — it raises the owner-facing ask."""
    from app.core.runtime_state import get_house_party, set_house_party

    set_house_party(False)
    context = _desktop()
    result = await HousePartySkill().execute(
        {"engaged": True, "objective": "market crash"}, context
    )

    assert get_house_party() is False, "the tool engaged the protocol without authorization"
    assert context.extra["house_party_auth"] == {"objective": "market crash"}
    assert "uthorization window" in result


@pytest.mark.asyncio
async def test_engage_with_wrong_passphrase_is_refused():
    from app.core.runtime_state import get_house_party, set_house_party

    set_house_party(False)
    context = _desktop()
    result = await HousePartySkill().execute(
        {"engaged": True, "passphrase": "not-the-phrase"}, context
    )

    assert get_house_party() is False
    assert "REFUSED" in result
    assert "house_party_auth" not in context.extra


@pytest.mark.asyncio
async def test_engage_with_correct_passphrase_engages():
    from app.config import settings
    from app.core.runtime_state import get_house_party, set_house_party

    set_house_party(False)
    result = await HousePartySkill().execute(
        {"engaged": True, "passphrase": settings.house_party_passphrase}, _desktop()
    )

    assert get_house_party() is True
    assert "ENGAGED" in result


@pytest.mark.asyncio
async def test_telegram_is_refused_and_never_promised_a_window():
    """Telegram has no authorization window — and no war room either, so the
    surface gate refuses it before the passphrase logic is reached. What matters
    either way is that no window is promised and nothing engages."""
    from app.core.runtime_state import get_house_party

    context = _context(trigger_payload={"channel": "telegram"},
                       extra={"client_platform": "telegram"})
    result = await HousePartySkill().execute({"engaged": True}, context)

    assert get_house_party() is False
    assert "house_party_auth" not in context.extra
    assert "uthorization window" not in result


@pytest.mark.asyncio
async def test_a_dispatched_agent_cannot_touch_the_protocol():
    from app.core.runtime_state import get_house_party, set_house_party

    set_house_party(True)
    result = await HousePartySkill().execute(
        {"engaged": False}, _context(triggered_by="agent")
    )

    assert get_house_party() is True
    assert "Refused" in result
