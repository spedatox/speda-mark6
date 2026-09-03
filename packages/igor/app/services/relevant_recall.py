# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Per-turn recall: the facts relevant to what he just said, injected without asking.

## The gap this closes

The observation store had two doors and both of them needed the model to open
them. `record_observation` had to be volunteered to write a fact (measured: 93
facts across 10,409 messages, which is why app/services/fact_extraction.py now
does it automatically), and `search_memory` has to be volunteered to read one.

The injected memory block does not close the reading half. It is the same
preloaded narrative files every turn, chosen before the owner has said anything
— so it carries what is ALWAYS relevant and nothing that is relevant NOW. A fact
recorded last week sits in the store, correct and findable, and never reaches
the prompt unless the agent independently decides to go looking. That is the
same failure mode as the write side: searching is not the task, answering is,
and under load the answer wins.

So this runs the owner's own message against the store on every turn and puts
what comes back in front of the model. Nothing to volunteer, nothing to
remember to do.

## Why this is safe to do every turn

  * **It cannot fabricate.** It only surfaces rows that already exist. The worst
    case is an irrelevant fact in the prompt, not a wrong one.
  * **It respects the floor.** Retrieval goes through `search_observations`, so
    `recall_min_similarity` applies and a question with no match injects
    NOTHING. That matters more than it sounds: an unconditional "here are the 8
    nearest facts" block would put noise in front of the model on every
    off-topic turn and teach it to ignore the section entirely.
  * **It does not touch the cached prefix.** The block is appended after the
    `_cache`-flagged blocks in the orchestrator, so a per-turn-varying section
    cannot invalidate the stable prompt prefix. See the block assembly in
    app/core/orchestrator.py.
  * **It is bounded.** `relevant_recall_limit` facts, `relevant_recall_max_chars`
    of text. The point is a short, high-signal reminder, not a second memory
    file.

## What it is not

It does not replace `search_memory`. This answers "what does the store hold
about what he just said"; the tool answers deliberate questions — a specific
person, a date range, the evidence behind a claim, what is well-established
versus observed once. This is the reflex; the tool is the investigation.
"""

import logging

logger = logging.getLogger(__name__)


def _extract_text(content) -> str:
    """Plain text out of an Anthropic content block array (or a string)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        return " ".join(p for p in parts if p)
    return ""


def latest_user_message(history) -> str:
    """The owner's most recent message — the query this turn retrieves against.

    Only the last one. Concatenating the conversation would blur the query into
    an average of everything discussed, which retrieves the CONVERSATION's
    general topic rather than the thing he just asked about, and the general
    topic is already in the context window.
    """
    for message in reversed(list(history or [])):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        text = _extract_text(message.get("content")).strip()
        if text:
            return text
    return ""


# A per-message timestamp is stamped onto user messages before they reach the
# model (see the time protocol in app/core/orchestrator.py). It is not part of
# what he asked, and leaving it in the query embeds a date into every search.
def _strip_stamp(text: str) -> str:
    import re

    return re.sub(r"^\[\d{4}-\d{2}-\d{2}[^\]]*\]\s*", "", text).strip()


async def facts_for_message(user_id: int, db, history, request_id: str = "") -> str:
    """The system block of facts relevant to this turn, or "" if there are none.

    Never raises. This is an enhancement to a prompt that was already valid
    without it, so a failure costs relevance, never the turn.
    """
    from app.config import settings
    from app.services.observations import format_observation, search_observations

    if not settings.relevant_recall_enabled or db is None:
        return ""

    query = _strip_stamp(latest_user_message(history))
    # A very short message ("ok", "yes", "devam") carries no retrievable intent
    # and would match on stopwords alone.
    if len(query) < settings.relevant_recall_min_query_chars:
        return ""

    try:
        scored = await search_observations(
            db,
            user_id=user_id,
            query=query[:2000],
            limit=settings.relevant_recall_limit,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "relevant_recall_failed",
            extra={"request_id": request_id, "error": str(e)},
        )
        return ""

    if not scored:
        return ""

    lines: list[str] = []
    used = 0
    for obs, _score in scored:
        # format_observation without a score: the number is meaningful to a tool
        # result the model asked for, and just noise in an unsolicited block.
        line = format_observation(obs)
        if used + len(line) > settings.relevant_recall_max_chars:
            break
        lines.append(line)
        used += len(line)

    if not lines:
        return ""

    logger.info(
        "relevant_recall_injected",
        extra={"request_id": request_id, "facts": len(lines), "chars": used},
    )
    return (
        "## Relevant to what he just said\n\n"
        "Facts already in the record that match his message this turn, retrieved "
        "automatically. Use them: he has told you these things before and should "
        "not have to again. They are RETRIEVED BY SIMILARITY, not by judgement, so "
        "an entry that turns out not to bear on the question is simply irrelevant "
        "— ignore it rather than working it into the answer. If what you need is "
        "not here, `search_memory` searches the whole record deliberately.\n\n"
        + "\n".join(lines)
    )
