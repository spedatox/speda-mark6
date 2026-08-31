# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Post-turn claim audit — did the agent say it DID something without doing it?

`prompts/core/05_output_policy.md` already carries the rule ("Past tense is a
claim, and needs a receipt"), every profile loads that file, and the models
violate it anyway: in the six days from 2026-07-29, 11 of 22 assistant messages
asserting a side effect ran ZERO tools. Five of those announced "House Party
Protocol engaged" while the flag was false the whole time. Advisory prompt text
has therefore already been tried and has already failed, which is the entire
reason this module is mechanical instead of another paragraph of prompt.

What it does NOT do, deliberately:
  - It does not block, rewrite, or re-run the turn. It runs as post-turn
    background work (Rule 7) and only ever writes a log line.
  - It does not call a model. A judge would cost a round trip per turn to
    police a minority of turns, and this needs to be cheap enough to leave on.
  - It does not try to match a claim to the tool that would satisfy it. That
    mapping is genuinely ambiguous ("noted" could be memory, a file write, or
    nothing at all), and a wrong mapping produces confident false positives.

So the rule is deliberately blunt: a past-tense side-effect claim in a turn that
called NO tool whatsoever. That is the shape that is unambiguous — nothing ran,
so nothing can have been recorded. A turn that called some tool is left alone
even if it called the wrong one; catching those needs judgement this cannot have.

Calibrated against all 1,416 assistant messages since 2026-07-01: it flags 15
(1.1% of turns), of which ~10 are genuine — the five House Party announcements,
Atomix claiming a PDF it never generated and a protocol "logged to sessions.md",
and Orion filing four projects "in projects.md" with no tool call. The remaining
~5 are long-form prose where the phrase occurs descriptively. Roughly two thirds
precision, which is the trade accepted for a signal that is free to leave on. If
that ratio drifts, tighten _CLAIM_PATTERNS — do not widen it.

Read the log with:  grep claim_without_tool
"""

import json
import logging
import re

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.message import Message

logger = logging.getLogger(__name__)

# Past-tense assertions that a side effect has already happened. Each must be
# unambiguous on its own — anything that reads as commentary rather than a
# receipt ("noted your preference for brevity") is a false positive, so the
# patterns stay tight and the offer/future filter below removes the rest.
# A bare acknowledgement is NOT one of these. Validated against 1,416 real prod
# messages: "Noted." / "Logged." / "Got it." on their own are this assistant's
# register for "understood", not a receipt for a write, and they accounted for
# ten of the first twenty-eight flags. What separates a claim from an
# acknowledgement is a DESTINATION (a file, the calendar, memory) or an explicit
# first-person completed action — so that is what these match.
_CLAIM_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bi'?ve (saved|stored|noted|logged|recorded|added|set|created|updated|scheduled|filed|dispatched|written)\b", "ive_done"),
    (r"\b(saved|stored|logged|recorded|filed|written) (it|that|this|them)?\s*(in|to|into) [\w./'\"]", "written_to"),
    (r"\b(reminder|alarm|event) (is )?(set|created|scheduled)\b", "reminder_set"),
    (r"\b(added|appended) (it|that|this) to\b", "added_to"),
    (r"\bit'?s (now )?(in|on) your (calendar|list|memory|file)\b", "its_in"),
    (r"\bconsider it (done|noted|handled)\b", "consider_done"),
    (r"\bhouse party protocol (is |has been )?engaged\b", "hpp_engaged"),
    (r"\b(i'?ve )?dispatched (it |that )?to\b", "dispatched"),
    (r"\bhas been (logged|saved|filed|recorded|written) (in|to)\b", "has_been"),
)

# If the matched sentence is an OFFER or a future intention, it is not a claim —
# "want me to save that?" and "I'll note it once you confirm" are both correct
# behaviour under the output policy, and flagging them would train the owner to
# ignore this log.
_NOT_A_CLAIM = re.compile(
    r"\b(want me to|shall i|should i|do you want|would you like|if you want|"
    r"i'?ll|i will|i can|let me know|say the word|once you|when you|"
    # Negations. "the data wasn't logged" is the model REPORTING a gap — the
    # opposite of a false claim, and flagging it inverts the whole point.
    r"n'?t|not |never |no longer |failed to|unable to|couldn'?t)\b",
    re.I,
)

_MAX_SNIPPET = 160


def _sentence_around(text: str, index: int) -> str:
    """The sentence the match landed in — the unit the offer filter judges."""
    start = max(text.rfind(".", 0, index), text.rfind("\n", 0, index)) + 1
    end = min(
        (p for p in (text.find(".", index), text.find("\n", index)) if p != -1),
        default=len(text),
    )
    return text[start : end + 1].strip()


def find_claims(text: str) -> list[str]:
    """Kinds of side-effect claim asserted in `text`. Empty when it asserts none."""
    if not text:
        return []
    low = text.lower()
    found: list[str] = []
    for pattern, kind in _CLAIM_PATTERNS:
        for match in re.finditer(pattern, low, re.M):
            if _NOT_A_CLAIM.search(_sentence_around(low, match.start())):
                continue
            found.append(kind)
            break
    return found


def split_message(raw: str) -> tuple[str, list[str]]:
    """(visible text, tool names) from a stored assistant message's content."""
    try:
        blocks = json.loads(raw)
    except Exception:
        return "", []
    if not isinstance(blocks, list):
        return "", []
    text: list[str] = []
    tools: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text.append(block.get("text") or "")
        elif block.get("type") == "_speda_meta":
            for tool in block.get("tools") or []:
                if isinstance(tool, dict) and tool.get("name"):
                    tools.append(tool["name"])
    return "\n".join(text), tools


async def audit_last_turn(session_id: int, request_id: str, agent_id: str = "") -> None:
    """Log a warning if this turn's reply claimed an action and ran no tools.

    Never raises: an audit that can break a turn is worse than no audit.
    """
    try:
        async with AsyncSessionLocal() as db:
            row = (
                await db.execute(
                    select(Message)
                    .where(Message.session_id == session_id, Message.role == "assistant")
                    .order_by(Message.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        if row is None:
            return

        text, tools = split_message(row.content)
        if tools:
            return  # something ran — out of this audit's competence
        claims = find_claims(text)
        if not claims:
            return

        logger.warning(
            "claim_without_tool",
            extra={
                "request_id": request_id,
                "session_id": session_id,
                "agent_id": agent_id,
                "message_id": row.id,
                "claims": sorted(set(claims)),
                "snippet": " ".join(text.split())[:_MAX_SNIPPET],
            },
        )
    except Exception as e:  # noqa: BLE001 — audit failure must never surface
        logger.debug("claim_audit_failed", extra={"error": str(e)})
