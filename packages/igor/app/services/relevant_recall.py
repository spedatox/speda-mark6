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

import json
import logging
import re

logger = logging.getLogger(__name__)

_MAX_EVIDENCE_ITEMS = 8
_MAX_EVIDENCE_ITEM_CHARS = 120
_MAX_DERIVED_QUERY_CHARS = 2000
_STAMP_RE = re.compile(r"^\[\d{4}-\d{2}-\d{2}[^\]]*\]\s*")
_DATE_RE = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?|"
    r"\d{1,2}:\d{2})\b"
)
_ENTITY_RE = re.compile(
    r"(?<![\w@])(?:[A-ZÇĞİÖŞÜ][\wÇĞİÖŞÜçğıöşü'’-]{2,}"
    r"(?:\s+[A-ZÇĞİÖŞÜ][\wÇĞİÖŞÜçğıöşü'’-]{2,}){0,3})"
)
_MEANINGFUL_KEY_RE = re.compile(
    r"(?:name|title|person|people|attendee|organizer|organization|company|project|"
    r"location|place|city|address|date|time|start|end|event|meeting|deadline|due|"
    r"commitment|decision|status|state|relationship|conflict)",
    re.IGNORECASE,
)
_ANCHOR_KEY_RE = re.compile(r"(?:name|title|subject|label|task|event|project)", re.IGNORECASE)
_RELATION_KEY_RE = re.compile(
    r"(?:status|state|deadline|due|decision|relationship|conflict|availability|change|result)",
    re.IGNORECASE,
)
_STATE_CHANGE_RE = re.compile(
    r"\b(?:moved|extended|approved|cancelled|canceled|blocked|unavailable|"
    r"rescheduled|completed|rejected|delayed|changed|closed|opened|assigned|"
    r"confirmed|postponed|paused|failed|passed|depends?\s+on|blocked\s+by)\b",
    re.IGNORECASE,
)


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
    return _STAMP_RE.sub("", text).strip()


def initial_recall_query(history) -> str:
    """The bounded objective used by both initial and adaptive recall."""
    return _strip_stamp(latest_user_message(history))[:_MAX_DERIVED_QUERY_CHARS]


def _short(value) -> str:
    text = " ".join(str(value).split())
    return text[:_MAX_EVIDENCE_ITEM_CHARS].strip(" ,;:-")


def salient_evidence(result, *, known_text: str = "") -> list[str]:
    """Extract a tiny, deterministic evidence frontier from a tool result.

    Structured fields with relevance-bearing names are preferred. For plain
    text, dates/times and proper-name-like phrases provide conservative seeds.
    This is deliberately not a summary and never feeds the raw result to search.
    """
    candidates: list[str] = []
    value = result
    if isinstance(result, str):
        raw = result.strip()
        if not raw or raw.lower() in {"(no output)", "none", "null", "[]", "{}"}:
            return []
        try:
            value = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            value = None
            for fragment in re.split(r"[\r\n.!?]+", raw[:12000]):
                if _STATE_CHANGE_RE.search(fragment):
                    candidates.append(_short(fragment))
            candidates.extend(_DATE_RE.findall(raw[:12000]))
            candidates.extend(_ENTITY_RE.findall(raw[:12000]))

    def walk(node, key: str = "", depth: int = 0) -> None:
        if depth > 4 or len(candidates) >= _MAX_EVIDENCE_ITEMS * 4:
            return
        if isinstance(node, dict):
            anchor = next(
                (
                    _short(child)
                    for child_key, child in node.items()
                    if _ANCHOR_KEY_RE.search(str(child_key))
                    and not isinstance(child, (dict, list))
                    and _short(child)
                ),
                "",
            )
            for child_key, child in node.items():
                if (
                    _RELATION_KEY_RE.search(str(child_key))
                    and not isinstance(child, (dict, list))
                    and _short(child)
                ):
                    relation = _short(f"{child_key} {_short(child)}")
                    candidates.append(_short(f"{anchor} -> {relation}") if anchor else relation)
            for child_key, child in node.items():
                walk(child, str(child_key), depth + 1)
        elif isinstance(node, list):
            for child in node[:20]:
                walk(child, key, depth + 1)
        elif node is not None and (_MEANINGFUL_KEY_RE.search(key) or _DATE_RE.search(str(node))):
            text = _short(node)
            if text:
                candidates.append(text)

    if value is not None:
        walk(value)

    known = {part.casefold() for part in re.findall(r"[\wÇĞİÖŞÜçğıöşü'’-]+", known_text)}
    seen: set[str] = set()
    evidence: list[str] = []
    for candidate in candidates:
        normalized = _short(candidate).casefold()
        if not normalized or normalized in seen:
            continue
        words = set(re.findall(r"[\wÇĞİÖŞÜçğıöşü'’-]+", normalized))
        if words and words.issubset(known):
            continue
        seen.add(normalized)
        evidence.append(_short(candidate))
        if len(evidence) >= _MAX_EVIDENCE_ITEMS:
            break
    return evidence


def derived_recall_query(objective: str, evidence: list[str]) -> str:
    """Combine the original objective and frontier without raw tool payloads."""
    pieces = [objective.strip(), *(_short(item) for item in evidence)]
    return " | ".join(piece for piece in pieces if piece)[:_MAX_DERIVED_QUERY_CHARS]


async def facts_for_query(user_id: int, db, query: str, request_id: str = "") -> str:
    """Run the existing bounded observation search for an explicit query."""
    from app.config import settings
    from app.services.observations import format_observation, search_observations

    query = query.strip()[:_MAX_DERIVED_QUERY_CHARS]
    if not settings.relevant_recall_enabled or db is None:
        return ""
    if len(query) < settings.relevant_recall_min_query_chars:
        return ""

    logger.info("relevant_recall_query", extra={"request_id": request_id, "query": query})
    try:
        scored = await search_observations(
            db, user_id=user_id, query=query, limit=settings.relevant_recall_limit,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("relevant_recall_failed", extra={"request_id": request_id, "error": str(e)})
        return ""

    lines: list[str] = []
    used = 0
    for obs, _score in scored:
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
    return "\n".join(lines)


async def facts_for_message(user_id: int, db, history, request_id: str = "") -> str:
    """The system block of facts relevant to this turn, or "" if there are none.

    Never raises. This is an enhancement to a prompt that was already valid
    without it, so a failure costs relevance, never the turn.
    """
    from app.config import settings

    if not settings.relevant_recall_enabled or db is None:
        return ""

    query = initial_recall_query(history)
    # A very short message ("ok", "yes", "devam") carries no retrievable intent
    # and would match on stopwords alone.
    if len(query) < settings.relevant_recall_min_query_chars:
        return ""

    facts = await facts_for_query(user_id, db, query, request_id=request_id)
    if not facts:
        return ""
    return (
        "## Relevant to what he just said\n\n"
        "Facts already in the record that match his message this turn, retrieved "
        "automatically. Use them: he has told you these things before and should "
        "not have to again. They are RETRIEVED BY SIMILARITY, not by judgement, so "
        "an entry that turns out not to bear on the question is simply irrelevant "
        "— ignore it rather than working it into the answer. If what you need is "
        "not here, `search_memory` searches the whole record deliberately.\n\n"
        + facts
    )
