"""
The post-turn claim audit.

Most of these are FALSE-POSITIVE tests, on purpose. A detector that cries wolf
gets ignored within a week, and the output policy explicitly wants offers ("want
me to save that?") and future intentions ("I'll note it once you confirm") — so
flagging those would punish exactly the behaviour the prompt asks for.

The true-positive cases are the real messages this shipped in response to, taken
verbatim from prod on 2026-08-04.
"""

import json

import pytest

from app.services.claim_audit import find_claims, split_message


def _stored(text: str, tools: list[str] | None = None) -> str:
    """A stored assistant message, in the shape the DB actually holds."""
    blocks: list[dict] = [{"type": "text", "text": text}]
    if tools is not None:
        blocks.append({
            "type": "_speda_meta",
            "tools": [{"id": f"t{i}", "name": n} for i, n in enumerate(tools)],
            "files": [],
        })
    return json.dumps(blocks)


# ── True positives: the messages that caused this ───────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "**House Party Protocol engaged.** All six agents standing by. What's the objective, sir?",
        "House Party Protocol engaged, sir. All six agents standing by. Awaiting your objective.",
        "House Party Protocol engaged. All six agents are online and standing by. State the objective, sir.",
    ],
)
def test_the_actual_prod_confabulations_are_caught(text):
    assert "hpp_engaged" in find_claims(text)


@pytest.mark.parametrize(
    "text,kind",
    [
        ("I've saved that to your file.", "ive_done"),
        ("Reminder set for 8am tomorrow.", "reminder_set"),
        ("Consider it done, sir.", "consider_done"),
        ("Added that to your calendar.", "added_to"),
        ("It's now in your memory file.", "its_in"),
        ("I've dispatched it to Sentinel.", "ive_done"),
        # Verbatim prod shapes — Orion and Atomix claiming file writes.
        ("Logged. **ATLAS Mark I** filed in projects.md — the server inventory app.", "written_to"),
        ("The weekly protocol has been logged to sessions.md.", "has_been"),
        ("I've created a PDF with a full-body training session.", "ive_done"),
    ],
)
def test_side_effect_claims_are_caught(text, kind):
    assert kind in find_claims(text)


# ── False positives: everything the output policy WANTS ─────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "Want me to save that to your file?",
        "I'll note it once you confirm the date.",
        "Shall I set a reminder for that?",
        "I can log this if you want it on the record.",
        "Do you want me to add that to your calendar?",
        "Let me know and I'll record it.",
        "Would you like me to dispatch that to Sentinel?",
        "I'll add it to your list once you've picked a time.",
        "Say the word and it's logged.",
        # Plain analysis with no assertion of a side effect at all.
        "Inflation is 31.75% and the rent cap is 31.9%, so your renewal lands near the cap.",
        "The gym renewal is due today — same Çalı gym as last year.",
        "House Party Protocol is not engaged right now.",
    ],
)
def test_offers_and_future_intent_are_not_claims(text):
    assert find_claims(text) == [], f"false positive on: {text}"


@pytest.mark.parametrize(
    "text",
    [
        "Noted.",
        "Noted. Go work.",
        "Noted. The GitHub deploys and Jotform are there if you want them later.",
        "Logged.",
        "Got it.",
        "Noted. Smart Calendar isn't live — corrected.",
    ],
)
def test_bare_acknowledgements_are_not_claims(text):
    """This assistant says "Noted." to mean "understood", not "written to disk".

    Ten of the first twenty-eight flags on the real corpus were this shape.
    A receipt names a destination or an explicit completed action; an
    acknowledgement does neither.
    """
    assert find_claims(text) == [], f"false positive on: {text}"


@pytest.mark.parametrize(
    "text",
    [
        "July 20 was completed — but the data wasn't logged. That's the gap.",
        "The session was never logged to sessions.md.",
        "I couldn't save that to your file — the store was unreachable.",
        "That is not logged anywhere yet.",
        "The tool failed to write it to memory.",
    ],
)
def test_reporting_that_something_was_NOT_done_is_not_a_claim(text):
    """The model flagging a gap is the output policy working, not failing."""
    assert find_claims(text) == [], f"false positive on: {text}"


def test_empty_text_is_not_a_claim():
    assert find_claims("") == []
    assert find_claims("   ") == []


# ── Message parsing ─────────────────────────────────────────────────────────


def test_split_message_reads_text_and_tools():
    text, tools = split_message(_stored("Saved it.", ["memory", "health_data"]))
    assert text == "Saved it."
    assert tools == ["memory", "health_data"]


def test_split_message_handles_a_turn_with_no_tools():
    text, tools = split_message(_stored("Noted."))
    assert text == "Noted."
    assert tools == []


@pytest.mark.parametrize("raw", ["not json", "{}", "[1, 2, 3]", ""])
def test_split_message_never_raises_on_junk(raw):
    assert split_message(raw) == ("", [])


# ── The audit itself ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_audit_flags_a_claim_with_no_tools(monkeypatch, caplog):
    import logging

    from app.services import claim_audit

    class _Row:
        id = 17663
        content = _stored("**House Party Protocol engaged.** All six agents standing by.")

    _patch_lookup(monkeypatch, claim_audit, _Row())
    with caplog.at_level(logging.WARNING):
        await claim_audit.audit_last_turn(954, "req-1", "speda")

    assert any(r.message == "claim_without_tool" for r in caplog.records)


@pytest.mark.asyncio
async def test_audit_stays_quiet_when_a_tool_ran(monkeypatch, caplog):
    import logging

    from app.services import claim_audit

    class _Row:
        id = 1
        content = _stored("I've saved that to your file.", ["memory"])


    _patch_lookup(monkeypatch, claim_audit, _Row())
    with caplog.at_level(logging.WARNING):
        await claim_audit.audit_last_turn(1, "req-2", "speda")

    assert not any(r.message == "claim_without_tool" for r in caplog.records)


@pytest.mark.asyncio
async def test_audit_never_raises_when_the_db_is_gone(monkeypatch):
    from app.services import claim_audit

    def _boom(*a, **k):
        raise RuntimeError("no database")

    monkeypatch.setattr(claim_audit, "AsyncSessionLocal", _boom)
    await claim_audit.audit_last_turn(1, "req-3")  # must not raise


def _patch_lookup(monkeypatch, module, row):
    """Stand in for the single SELECT the audit makes."""

    class _Result:
        def scalar_one_or_none(self):
            return row

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, _stmt):
            return _Result()

    monkeypatch.setattr(module, "AsyncSessionLocal", lambda: _Session())
