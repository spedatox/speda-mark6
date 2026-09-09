# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Automatic fact extraction — the reason "I told you this already" kept happening.

## The gap this closes

Until this existed, a fact entered the observation store in exactly one way: the
model decided, mid-conversation, to call `record_observation`. Measured on
2026-09-03, that had produced **93 recorded facts across 10,409 user messages** —
roughly one fact for every 112 things the owner said.

Everything else in recall is downstream of that number. A relevance floor, a
hybrid ranker and a repaired store are all ways of finding what was written
down; none of them help with what was never written down at all. The owner's
complaint — that he has to keep repeating himself — was never mainly a retrieval
failure. It was a WRITE failure wearing a retrieval failure's clothes.

Relying on the model to volunteer the call is a bad mechanism for a predictable
reason: recording is never the task. The agent is answering a question, and the
tool call that would make the answer unnecessary next time competes for
attention with the answer itself. Under any load at all, the answer wins. So
this runs as post-turn background work (Rule 7) instead of asking politely.

## What it does

After each exchange, the last user/assistant pair is read and the model is asked
for the durable facts the OWNER stated. Everything then goes through the same
gates a hand-written observation faces, plus one more:

  * **Owner-stated only.** Facts are extracted from what HE said, not from what
    the assistant proposed. An assistant's suggestion recorded as a fact is how
    a store fills up with its own speculation and then cites it back as
    evidence.
  * **Grounded.** Every proper noun and number in an extracted fact must appear
    in the exchange it came from. The same guard the distillation migration
    uses, and for the same reason: given thin input, a model does not return an
    empty list, it invents a plausible one.
  * **Validated.** Routed through `record_observations`, so the fragment guard,
    the evidence ladder and the subject normalisation all apply. Nothing enters
    here that could not have been written by an agent by hand.
  * **Deduplicated into reinforcement.** `record_observations` already treats a
    repeat of the same normalised content as reinforcement rather than a new
    row, so a fact he mentions across three conversations becomes ONE well-
    established fact instead of three competing near-duplicates. That is a
    feature of running this every turn, not a cost of it.
  * **Capped.** `auto_extract_max_facts` per turn. A turn that appears to
    contain twenty durable facts is nearly always a long answer being mined for
    trivia, and flooding the store is precisely how recall was broken the first
    time.

## What it deliberately does not do

It does not touch the /memories narrative files. Those are the owner's to read
and the roster's to write deliberately; this tier is the addressable fact
beneath them (see app/skills/observations.py). And it records at level
`explicit` only — a background pass gets no vote on deductions, which need
cited premises and a model that is actually reasoning about the record.
"""

import json
import logging
import re

logger = logging.getLogger(__name__)

# Extracted facts are attributed to this observer rather than to the agent that
# happened to be talking. Attribution in this store means "which agent judged
# this worth recording", and a background pass made no judgement of the kind
# `observer` is meant to capture — convergence between agents is the strongest
# signal in the record, and it would be worthless if a background task could
# manufacture it on every agent's behalf.
OBSERVER = "auto"

_PROMPT = """\
Below is one exchange between the owner (Ahmet Erol Bayrak) and his assistant.
Extract the durable facts THE OWNER stated about himself, his life, his people
or his projects.

Extract a fact only if ALL of these hold:

- THE OWNER said it. Ignore everything the assistant asserted, suggested,
  offered or speculated. If he did not say it or explicitly confirm it, it is
  not a fact.
- It is DURABLE. It should still be worth knowing in six months. A preference,
  a decision, a constraint, a name, a figure, a date, a change in his
  circumstances. NOT what he is doing this minute, NOT his mood, NOT the
  mechanics of the current task.
- It is SELF-CONTAINED. One English sentence naming its subject, that answers a
  question when read alone with none of this conversation around it. Write
  "Ahmet Erol Bayrak's midterm for BLM2107 is on 12 November 2026", never "The
  midterm is next Thursday".
- Every name and number in it appears in the exchange below. Invent nothing and
  infer nothing.
- A RELATIVE date ("last week", "next Thursday") is not a reason to discard the
  fact — it is a reason to leave the date out. Record "Ahmet Erol Bayrak had his
  Samsung S24 serviced at Electropazar for 2,400 TL" and simply omit the "last
  week". Never convert a relative date into an absolute one you were not given.

For each fact give:
  domain  — biography, preference, state, project, training, finance, or event.
  subject — "owner", "person:<Name>", or "project:<Name>".
  quote — the exact supporting words copied verbatim from OWNER.
  Use event for completed actions, state only for ongoing situations. Never default an unknown domain to state.

Return AT MOST {max_facts} facts. Many exchanges contain none at all — small
talk, a thank-you, a question he asked — and [] is then the correct answer.
But do not be timid: if he stated something durable, RECORD IT. A fact missed
here is a fact he has to tell someone twice.

EXCHANGE:
OWNER: {user_message}


Return ONLY a JSON array:
[{{"text": "<the fact>", "domain": "state", "subject": "owner", "quote": "<verbatim owner evidence>"}}]
"""

_NUMBERS = re.compile(r"\d[\d.,:/]*\d|\d")
_PROPER = re.compile(r"\b[A-ZÇĞİÖŞÜ][\w'’-]{2,}\b")

# Same rationale as scripts/distill_memory_files.py: sentence-initial and common
# words are not evidence of anything, and requiring them in the source would
# reject sound facts over the word "The".
_PROPER_STOP = frozenset({
    "the", "he", "his", "him", "she", "her", "they", "their", "it", "its",
    "this", "that", "these", "those", "and", "but", "for", "with", "from",
    "after", "before", "during", "between", "when", "while", "because",
    "ahmet", "erol", "bayrak", "owner", "both", "one", "two", "three",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december", "turkish", "english",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
})

_FOLD = str.maketrans({
    "ı": "i", "İ": "i", "I": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
    "ç": "c", "Ç": "c", "ö": "o", "Ö": "o", "ü": "u", "Ü": "u",
})


def _fold(value: str) -> str:
    return (value or "").translate(_FOLD).lower()


def ungrounded_tokens(fact: str, source: str) -> list[str]:
    """Tokens in `fact` absent from `source`. Empty means grounded.

    Folded on both sides, so a fact written "Uludag" matches a source that said
    "Uludağ" — this tests for invention, not for spelling drift.
    """
    haystack = _fold(source)
    plain_source = re.sub(r"[.,:/]", "", source)
    missing: list[str] = []
    for token in _NUMBERS.findall(fact):
        if token not in source and re.sub(r"[.,:/]", "", token) not in plain_source:
            missing.append(token)
    for token in _PROPER.findall(fact):
        folded = _fold(re.sub(r"['’]s$", "", token))
        if folded in _PROPER_STOP:
            continue
        if folded not in haystack:
            missing.append(token)
    return missing


def _parse_json_array(raw: str) -> list[dict]:
    body = (raw or "").strip()
    if body.startswith("```"):
        body = re.sub(r"^```[a-z]*\s*", "", body)
        body = re.sub(r"\s*```$", "", body)
    start, end = body.find("["), body.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        return [p for p in json.loads(body[start : end + 1]) if isinstance(p, dict)]
    except json.JSONDecodeError:
        return []


async def extract_turn_facts(
    session_id: int,
    request_id: str,
    user_id: int,
    model: str,
) -> None:
    """Post-turn hook: record the durable facts the owner stated this exchange.

    Never raises. This is background work behind a completed response, and a
    failure here must cost the owner a fact, never a turn.
    """
    from app.config import settings
    from app.database import AsyncSessionLocal
    from app.services.llm_client import LLMClient
    from app.services.memory import _load_last_exchange
    from app.services.observations import record_observations

    if not settings.auto_extract_facts:
        return

    try:
        async with AsyncSessionLocal() as db:
            user_msg, assistant_msg = await _load_last_exchange(db, session_id)
            # A queued job must read the exchange it was created for, not the
            # newest message present when a delayed drain eventually runs.
            from sqlalchemy import select
            from app.models.background_job import BackgroundJob
            from app.models.message import Message
            from app.models.session import Session
            session = (await db.execute(select(Session).where(
                Session.id == session_id, Session.user_id == user_id,
            ))).scalar_one_or_none()
            if session is None or session.triggered_by != "user":
                return  # Workflow/agent seeds are not statements by the owner.
            job = (await db.execute(select(BackgroundJob).where(
                BackgroundJob.session_id == session_id,
                BackgroundJob.request_id == request_id,
                BackgroundJob.kind == "extract_facts",
            ).order_by(BackgroundJob.id.desc()).limit(1))).scalar_one_or_none()
            source_id = (job.payload or {}).get("source_message_id") if job else None
            if source_id:
                message = (await db.execute(select(Message).where(
                    Message.id == source_id, Message.session_id == session_id,
                    Message.role == "user",
                ))).scalar_one_or_none()
                if message is None:
                    raise ValueError("Queued extraction evidence message is missing.")
                user_msg = (" ".join(b.get("text", "") for b in message.content
                            if isinstance(b, dict) and b.get("type") == "text")
                            if isinstance(message.content, list) else str(message.content))
            if not user_msg.strip():
                return

            extraction_model = (
                settings.auto_extract_model
                or settings.llm_background_model
                or model
            )
            resp = await LLMClient().create_message(
                model=extraction_model,
                system=(
                    "You extract durable facts from conversation. You return only "
                    "the JSON array asked for, and [] when there is nothing."
                ),
                messages=[{
                    "role": "user",
                    "content": _PROMPT.format(
                        max_facts=settings.auto_extract_max_facts,
                        user_message=user_msg[:6000],
                    ),
                }],
                max_tokens=2048,
                # A reasoning model otherwise spends the budget thinking and
                # returns nothing — the trap app/services/memory.py documents
                # for title generation. This is extraction, not deliberation.
                reasoning_effort="minimal",
            )
            raw = resp.content[0].text if resp.content else ""

            exchange = user_msg
            proposals, ungrounded = [], 0
            for item in _parse_json_array(raw)[: settings.auto_extract_max_facts]:
                text = str(item.get("text") or "").strip()
                if not text:
                    continue
                quote = str(item.get("quote") or "").strip()
                if not quote or quote not in user_msg or ungrounded_tokens(text, quote):
                    ungrounded += 1
                    continue
                proposals.append({
                    "content": text,
                    "level": "explicit",
                    "domain": str(item.get("domain") or "event").strip().lower(),
                    "subject": str(item.get("subject") or "owner").strip(),
                    "sources": [f"message:{source_id}: {quote}" if source_id else f"session:{session_id}: {quote}"],
                })

            if not proposals:
                if ungrounded:
                    logger.info(
                        "auto_extract_all_ungrounded",
                        extra={"request_id": request_id, "dropped": ungrounded},
                    )
                return

            stored, rejections = await record_observations(
                db,
                user_id=user_id,
                observer=OBSERVER,
                proposals=proposals,
                session_id=session_id,
                message_ids=[source_id] if source_id else None,
                request_id=request_id,
            )
            reinforced = sum(1 for o in stored if o.reinforcement_count > 1)
            logger.info(
                "auto_extract_facts",
                extra={
                    "request_id": request_id,
                    "session_id": session_id,
                    "stored": len(stored),
                    "reinforced": reinforced,
                    "ungrounded": ungrounded,
                    "rejected": len(rejections),
                },
            )

    except Exception as e:  # noqa: BLE001
        logger.error(
            "auto_extract_error",
            extra={"request_id": request_id, "session_id": session_id, "error": str(e)},
        )
        # Let the durable queue retry and expose failures, instead of recording
        # a successful job after a swallowed provider/database exception.
        raise
