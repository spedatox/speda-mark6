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

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, replace

logger = logging.getLogger(__name__)

_MAX_EVIDENCE_ITEMS = 8
_MAX_EVIDENCE_ITEM_CHARS = 120
_MAX_DERIVED_QUERY_CHARS = 2000
_STAMP_RE = re.compile(r"^\[(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+)?\d{4}-\d{2}-\d{2}[^\]]*\]\s*")
_COORD_PAIR_RE = re.compile(r"(?<![\d.])(-?\d{1,3}\.\d{3,})\s*,\s*(-?\d{1,3}\.\d{3,})(?![\d.])")
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


def coordinate_anchors(text: str) -> set[tuple[float, float]]:
    """Exact numeric coordinate pairs for retrieval, not a place inference."""
    return {(float(lat), float(lng)) for lat, lng in _COORD_PAIR_RE.findall(text or "")
            if -90 <= float(lat) <= 90 and -180 <= float(lng) <= 180}


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
            lat = node.get("latitude", node.get("lat"))
            lng = node.get("longitude", node.get("lng", node.get("lon")))
            if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
                if -90 <= lat <= 90 and -180 <= lng <= 180:
                    candidates.append(f"coordinates {lat}, {lng}")
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
    from app.services.observations import (
        format_observation, related_observations, search_observations,
    )

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
    # A query can hit the event but miss a person mentioned in that same owner
    # message. Expand only the strongest two hits, and spend the SAME char
    # budget. This is provenance linkage, never an inferred causal claim.
    included = {obs.id for obs, _score in scored[:len(lines)]}
    for obs, _score in scored[:2]:
        try:
            related = await related_observations(
                db, user_id=user_id, observation_id=obs.id, limit=3,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("related_recall_failed", extra={
                "request_id": request_id, "observation_id": obs.id,
                "error": str(exc),
            })
            continue
        for neighbour, reasons in related["links"]:
            if neighbour.id in included:
                continue
            line = f"[Related to id:{obs.id} via {', '.join(reasons)}] {format_observation(neighbour)}"
            if used + len(line) > settings.relevant_recall_max_chars:
                continue
            lines.append(line)
            included.add(neighbour.id)
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


_FOLLOWUP = re.compile(
    r"\b(it|that|this|right|again|last conversation|last time|remember|yup|yes|nope|"
    r"devam|haklı|hakli|evet|hayır|hayir|önceki|onceki|geçen|gecen|o|bu)\b", re.I)


@dataclass
class AutomaticRecall:
    facts: str = ""
    conversations: str = ""
    degraded: bool = False


async def recent_exchange(context, *, max_chars=2000, scope="own", existing_text=""):
    """A referent for a short new-session follow-up, even without a recap/index."""
    from sqlalchemy import select
    from app.models.message import Message
    from app.models.session import Session
    from app.services.chat_history import execution_receipts

    statement = select(Session).join(Message, Message.session_id == Session.id).where(
        Session.user_id == context.user_id,
        Session.id != context.session_id, Session.triggered_by == "user",
        Message.role == "user",
    )
    if scope == "own":
        statement = statement.where(Session.agent_id == context.agent_id)
    last = (await context.db.execute(statement.order_by(
        Message.created_at.desc(), Message.id.desc()).limit(1))).scalar_one_or_none()
    if last is None:
        return "", ""
    head = list((await context.db.execute(select(Message).where(
        Message.session_id == last.id, Message.role.in_(("user", "assistant")),
    ).order_by(Message.id).limit(8))).scalars())
    tail = list((await context.db.execute(select(Message).where(
        Message.session_id == last.id, Message.role.in_(("user", "assistant")),
    ).order_by(Message.id.desc()).limit(4))).scalars())
    messages = sorted({m.id: m for m in head + tail}.values(), key=lambda m: m.id)
    heading = f"Recent owner conversation · {last.agent_id} · session {last.id} (possible follow-up context)"
    lines, seed, selected_ids, used = [heading], [], [], len(heading) + 1
    for message in messages:
        text = _extract_text(message.content).strip()
        if not text or "[cancelled by owner]" in text:
            continue
        receipt = ""
        if message.role == "assistant":
            receipt = execution_receipts(message.content, budget=450)
        duplicate = len(text) >= 24 and text in existing_text
        if duplicate and not receipt:
            continue
        snippet = "[reply text already in context]" if duplicate else text[:400] + ("…" if len(text) > 400 else "")
        line = f"[{message.role} message:{message.id} {message.created_at.isoformat()}] {snippet}"
        if receipt:
            line += "\n" + receipt
        if used + len(line) + 1 > max_chars:
            continue
        lines.append(line)
        selected_ids.append(message.id)
        used += len(line) + 1
        if message.role == "user" and len(re.findall(r"\w+", text)) >= 3:
            seed.append(text[:250])
    context.extra.setdefault("recall_trace", []).append({
        "stage": "recent_exchange", "session_id": last.id,
        "candidate_message_ids": [m.id for m in messages], "message_ids": selected_ids,
    })
    return ("\n".join(lines) if len(lines) > 1 else ""), " ".join(seed)[:700]


async def recall_for_turn(context, *, scope="own", cache=None, existing_text="") -> AutomaticRecall:
    """Bounded read-only relevance search. Returns data; prompt policy stays in the orchestrator."""
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.config import settings
    from app.services import query_translation
    from app.services.embeddings import embed_texts
    from app.services.observations import format_observation, search_observations
    from app.skills.semantic_search import search_conversations, warm_conversation_vectors

    result = AutomaticRecall()
    if context.db is None or not settings.relevant_recall_enabled:
        return result
    query = initial_recall_query(context.conversation_history)
    if not query:
        return result
    meaningful = len(query) >= settings.relevant_recall_min_query_chars
    followup = len(query) <= 90 and bool(_FOLLOWUP.search(query))
    if not meaningful and not followup:
        return result
    visible = {_strip_stamp(_extract_text(m.get("content"))) for m in context.conversation_history
               if isinstance(m, dict)}
    visible_owner = {_strip_stamp(_extract_text(m.get("content"))) for m in context.conversation_history
                     if isinstance(m, dict) and m.get("role") == "user"}
    prior = [m for m in context.conversation_history[:-1] if isinstance(m, dict)]
    if followup and prior:
        query += " | " + " ".join(_strip_stamp(_extract_text(m.get("content")))[:350]
                                   for m in prior[-2:])
    start = time.monotonic()
    budget = max(0, settings.automatic_recall_max_chars)
    if not budget:
        return result
    deadline = max(1, settings.automatic_recall_timeout_ms) / 1000
    error = None
    network = None
    if cache is not None:
        # Build/refresh local vectors while provider preparation is in flight.
        # A cold load may finish beyond this turn's deadline for the next turn.
        warm_conversation_vectors(context, cache)
    # Cancellation of a slow read must not invalidate the chat's own DB transaction.
    async with AsyncSession(bind=context.db.bind, expire_on_commit=False) as db:
        read_context = replace(context, db=db)
        read_context.extra["memory_recall_cache"] = cache
        try:
            async with asyncio.timeout(deadline):
                if followup and not prior:
                    result.conversations, seed = await recent_exchange(read_context, max_chars=min(2000, budget), scope=scope, existing_text=existing_text)
                    if result.conversations:
                        read_context.extra["selected_conversation_windows"] = [result.conversations]
                    if seed:
                        query += " | " + seed
                query = query[:2000]

                async def prepare():
                    vector_text, lexical_text = await query_translation.expand(query)
                    return vector_text, lexical_text, (await embed_texts([vector_text]))[0]

                arguments = {"query": query, "limit": 3, "context_window": 1,
                    "_owner_sessions_only": True, "_visible_texts": visible, "_existing_text": existing_text,
                    "_receipt_chars": 450,
                    "agent_id": context.agent_id if scope == "own" else None}

                async def retrieve(prepared=None):
                    scored = await search_observations(db, user_id=context.user_id, query=query,
                        limit=settings.relevant_recall_limit, lexical_only=prepared is None,
                        prepared_query=prepared, diagnostics=context.extra)
                    lines, selected_ids = [], []
                    for observation, score in scored:
                        line = format_observation(observation)
                        if observation.message_ids:
                            line += "\n    message sources: " + ", ".join(
                                f"message:{mid}" for mid in observation.message_ids[:8])
                        body = observation.content.strip()
                        if body in existing_text or body in visible_owner or line in lines:
                            continue
                        if len("\n".join(lines + [line])) <= min(settings.relevant_recall_max_chars, budget):
                            lines.append(line)
                            selected_ids.append(observation.id)
                    if lines:
                        result.facts = "\n".join(lines)
                    read_context.extra.setdefault("recall_trace", []).append({
                        "stage": "facts", "lexical_only": prepared is None,
                        "candidate_ids": [o.id for o, _ in scored], "selected_ids": selected_ids,
                    })
                    text = await search_conversations(read_context, dict(arguments,
                        _lexical_only=prepared is None, _prepared_query=prepared))
                    if text and not text.startswith(("No ", "Recall is unavailable")):
                        result.conversations = text

                # Keep useful local evidence before waiting for any provider call.
                await retrieve()
                network = asyncio.create_task(prepare())
                try:
                    prepared = await network
                except Exception as exc:
                    error = type(exc).__name__
                    result.degraded = True
                else:
                    await retrieve(prepared)
        except TimeoutError:
            error, result.degraded = "deadline", True
        except Exception as exc:
            error, result.degraded = type(exc).__name__, True
        finally:
            if network is not None:
                if not network.done():
                    network.cancel()
                await asyncio.gather(network, return_exceptions=True)
    if context.extra.get("recall_errors"):
        result.degraded = True
        error = error or context.extra["recall_errors"][0]
    # Keep each exchange and its execution evidence together under the budget.
    room = max(0, budget - len(result.facts))
    selected, used = [], 0
    windows = context.extra.get("selected_conversation_windows", [result.conversations])
    for window in windows[:3]:
        if not window.strip() or used + len(window) + 1 > room:
            continue
        selected.append(window)
        used += len(window) + 1
    result.conversations = "\n".join(selected)
    elapsed = time.monotonic() - start
    deadline_missed = elapsed > deadline
    if deadline_missed:
        result.degraded = True
        error = error or "deadline"
    context.extra["automatic_recall"] = {
        "query": query, "elapsed_ms": round(elapsed * 1000, 1),
        "degraded": result.degraded, "error": error,
        "facts_chars": len(result.facts), "conversation_chars": len(result.conversations),
        "selected_windows": len(selected), "deadline_missed": deadline_missed,
    }
    logger.info("automatic_recall", extra={"request_id": context.request_id, **context.extra["automatic_recall"]})
    return result
