# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Observation store — the provenance-carrying substrate beneath /memories.

Rule 1 (no logic in routers) and the tier boundaries both apply: the skills in
app/skills/observations.py are thin argument-shaping wrappers; every rule about
what a valid observation IS lives here.

The design is ported from Honcho's observation model (plastic-labs/honcho,
src/utils/agent_tools.py) and adapted to Mark VI:

  - Honcho keys observations by an (observer, observed) peer pair. Mark VI is
    single-owner, so `observed` is always the owner and only `observer` (the
    agent_id) is stored — but it stays, because eight agents observing one owner
    is exactly the case where attribution matters.
  - Honcho enforces the evidence ladder in the TOOL SCHEMA with JSON-Schema
    if/then. We enforce it here as well, in Python, because a schema constraint
    is advisory across providers (Rule 8's wire format is Anthropic's, but the
    same skills run on OpenAI/Gemini/Ollama through llm_client) and a deduction
    that arrives with no premises must be rejected on every one of them.
  - Deletion is soft. docs/MEMORY_ARCHITECTURE.md §3.4 is explicit that owner
    knowledge is demoted, never destroyed; the observation store follows the
    same law rather than inventing a second one.

Deduplication is by exact normalized content per (user, observer, level): a
re-observed fact bumps `reinforcement_count` and refreshes provenance instead of
creating a near-duplicate row. This is what makes "what does he consistently
want?" answerable — repetition becomes a ranking signal rather than noise.
"""

import logging
import re
from datetime import datetime, timezone

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.models.observation import Observation

logger = logging.getLogger(__name__)

# ── What a fact is ABOUT (v3 §3.1) ────────────────────────────────────────────

# The domain vocabulary. Closed, like the file taxonomy it replaces — but it
# classifies the FACT rather than its destination, which is why it is shorter:
# "which of eight files" collapses into "what kind of thing is this", and the
# file falls out of that plus the subject plus the validity.
DOMAINS: tuple[str, ...] = (
    "biography",   # who someone is — durable, non-expiring background
    "preference",  # what the owner likes, dislikes, wants, and in what manner
    "state",       # something true of the owner's life right now
    "project",     # a project's status or progress
    "training",    # a gym session or training fact (Atomix's domain)
    "finance",     # an account, figure, budget or holding (Sentinel's domain)
    "event",       # a dated thing that happened, usually to another person
)

SUBJECT_KINDS: tuple[str, ...] = ("owner", "person", "project")


def normalize_subject(raw: str) -> str:
    """Canonical subject string: `owner`, `person:<Name>`, `project:<Name>`.

    Identity drift is the subject-side version of the file drift v2 fought:
    "person:Zeynep", "person: zeynep" and "person:Zeynep  " are three people as
    far as a renderer is concerned, and social.md would grow three sections for
    one person. Normalizing at the boundary is cheaper than reconciling later —
    and `record_observations` additionally reuses the exact spelling of an
    existing subject that differs only in case, so the FIRST spelling wins and
    the roster converges on it instead of drifting apart.
    """
    text = " ".join((raw or "").split()).strip()
    if not text:
        return "owner"
    if ":" not in text:
        return "owner" if text.lower() == "owner" else f"person:{text}"
    kind, _, name = text.partition(":")
    kind = kind.strip().lower()
    name = " ".join(name.split()).strip()
    if kind == "owner" or not name:
        return "owner"
    if kind not in SUBJECT_KINDS:
        kind = "person"
    return f"{kind}:{name}"


# ── The evidence ladder ───────────────────────────────────────────────────────

LEVELS: tuple[str, ...] = ("explicit", "deductive", "inductive", "contradiction")

PATTERN_TYPES: tuple[str, ...] = (
    "preference",
    "behavior",
    "personality",
    "tendency",
    "correlation",
)

CONFIDENCE_LEVELS: tuple[str, ...] = ("high", "medium", "low")

# Per-observation length cap. Honcho caps peer-card entries at 200 chars to stop
# evidence-bundle dumps; an observation carries more than a card entry but must
# still be ONE fact, not a paragraph of narrative that can never be deduped.
MAX_CONTENT_LENGTH = 600

# Minimum source counts per level — the structural half of the ladder.
_MIN_SOURCES: dict[str, int] = {
    "deductive": 1,
    "inductive": 2,
    "contradiction": 2,
}

MAX_SEARCH_CANDIDATES = 20_000  # perf guard on the brute-force scan

# ── The fragment guard ────────────────────────────────────────────────────────
# A store built by splitting memory files line-by-line fills with orphan bullets:
# "a table of incomes", "**Started:** 2026-08-01", "Keep entries dated by month."
# They are not facts. They mean nothing outside the file they were lifted from,
# they cannot answer a question, and — the reason this is a retrieval bug and not
# a tidiness one — a sentence with no subject embeds to a generic centroid, which
# is close to EVERY query. 60% of the store was fragments when this was measured,
# and they occupied the top of the ranking for questions they had no bearing on.
#
# So the write path now insists on a fact that stands alone. The checks are
# deliberately shallow and mechanical: whether the claim is TRUE is the prompt's
# problem (see validate_observation's docstring), whether it is a self-contained
# SENTENCE is something we can actually decide here.

# A markdown field label with no sentence around it: "**Version:** 5.0.0".
_FIELD_LABEL = re.compile(r"^\*{0,2}[\w \-/]{1,40}:\*{0,2}\s*\S{0,60}$")

# Something that names who or what the fact is about. Third-person pronouns
# count: "He has never flown" is self-contained in a store whose default subject
# is the owner. A bare noun phrase ("a table of incomes") is not.
_NAMES_SUBJECT = re.compile(
    r"\b(ahmet|erol|bayrak|owner|he|his|him|they|their|it|its|the\s+\w+)\b",
    re.IGNORECASE,
)

# NOTE: there is deliberately NO "does it contain a verb" check here. The obvious
# implementation is a whitelist of finite verbs, and measuring one against the
# real store showed why that is a trap: it rejected "Became engaged to İlayda
# Bayrak.", "Vertical pull other than the cable pulldown: never." and dozens of
# other perfectly good facts, because the set of verbs a fact can be built around
# is open and a whitelist is always a sample of it. Subject, length and shape are
# decidable; grammaticality is not, and a guard that guesses wrong on the write
# path silently loses facts.


def fragment_reason(text: str) -> str | None:
    """Why this content is a fragment rather than a fact, or None if it is fine.

    Returns the reason as a sentence written FOR THE MODEL, same contract as
    ObservationRejected: it is shown as the tool result, so it has to say what
    to do instead.
    """
    from app.config import settings

    body = (text or "").strip()
    if len(body) < settings.observation_min_content_length:
        return (
            f"it is {len(body)} characters — under the "
            f"{settings.observation_min_content_length}-character floor for a fact "
            f"that has to stand on its own six months from now"
        )
    if _FIELD_LABEL.match(body):
        return (
            "it is a field label lifted out of a document, not a sentence. "
            "'**Started:** 2026-08-01' answers nothing on its own; "
            "'The FORGE rebuild started on 2026-08-01' does"
        )
    if not settings.observation_require_subject:
        return None
    if not _NAMES_SUBJECT.search(body):
        return (
            "it never says who or what it is about. A fact whose subject lives "
            "in the file it was copied out of is unfindable once retrieved on "
            "its own — name him, the person, or the project explicitly"
        )
    return None


class ObservationRejected(Exception):
    """A proposed observation violated the evidence ladder.

    Raised with a message written FOR THE MODEL — it is returned as the tool
    result, so it must say precisely what was missing and how to fix it. A
    rejection the model cannot act on just becomes a retry loop.
    """


# ── Validation ────────────────────────────────────────────────────────────────

def _parse_day(value, field: str):
    """Accept a date, a datetime, or 'YYYY-MM-DD'. Reject anything else loudly.

    A relative or malformed date silently coerced to None would make a fact look
    permanently true, which is the one error the temporal model cannot survive.
    """
    from datetime import date as _date

    if value in (None, "", "null"):
        return None
    if isinstance(value, _date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ObservationRejected(
        f"Rejected: `{field}` must be an absolute date as YYYY-MM-DD — got "
        f"'{text}'. Relative dates ('last month', 'in the spring') stop being "
        f"true the moment they are stored; work out the actual date first."
    )


def validate_observation(
    *,
    content: str,
    level: str,
    subject: str = "owner",
    domain: str = "state",
    valid_from=None,
    valid_until=None,
    source_ids: list | None = None,
    premises: list | None = None,
    sources: list | None = None,
    pattern_type: str | None = None,
    confidence: str | None = None,
) -> dict:
    """
    Check one proposed observation and return it normalized. Raises
    ObservationRejected with an actionable message otherwise.

    This is form-only validation, deliberately. Whether the CLAIM is true is the
    prompt's problem; whether a deduction was handed its premises is ours, and
    that is the half a prompt cannot reliably enforce.
    """
    text = (content or "").strip()
    if not text:
        raise ObservationRejected("Rejected: `content` was empty. State the fact in one sentence.")
    if len(text) > MAX_CONTENT_LENGTH:
        raise ObservationRejected(
            f"Rejected: `content` is {len(text)} characters, over the "
            f"{MAX_CONTENT_LENGTH} cap. One observation is ONE fact — split it, "
            f"or record the durable part and drop the narration."
        )
    reason = fragment_reason(text)
    if reason is not None:
        raise ObservationRejected(
            f"Rejected: {reason}. Rewrite it as one self-contained English "
            f"sentence that names its subject and states what is true of it — "
            f"something that still answers a question when it is retrieved "
            f"alone, months from now, with none of this conversation around it. "
            f"Got: {text[:120]!r}"
        )

    lvl = (level or "explicit").strip().lower()
    if lvl not in LEVELS:
        raise ObservationRejected(
            f"Rejected: unknown level '{level}'. Use one of: {', '.join(LEVELS)}."
        )

    ids = [int(i) for i in (source_ids or []) if str(i).strip().lstrip("-").isdigit()]
    prem = [str(p).strip() for p in (premises or []) if str(p).strip()]
    srcs = [str(s).strip() for s in (sources or []) if str(s).strip()]

    article = "an" if lvl[0] in "aeiou" else "a"
    required = _MIN_SOURCES.get(lvl, 0)
    if required:
        if len(ids) < required:
            raise ObservationRejected(
                f"Rejected: {article} '{lvl}' observation requires at least {required} "
                f"`source_ids` (the id of each observation it rests on) — got "
                f"{len(ids)}. Search first with `search_memory`, then cite the "
                f"ids it returns. If you cannot cite sources, record this as "
                f"'explicit' instead."
            )
        # The human-readable half: deductive calls them premises, the other two
        # call them sources. Requiring both halves is what keeps a recall result
        # self-explaining without a second lookup per source.
        if lvl == "deductive" and len(prem) < required:
            raise ObservationRejected(
                f"Rejected: a 'deductive' observation requires at least {required} "
                f"`premises` — the readable text of each source you cited."
            )
        if lvl in ("inductive", "contradiction") and len(srcs) < required:
            raise ObservationRejected(
                f"Rejected: {article} '{lvl}' observation requires at least {required} "
                f"`sources` — the readable evidence text behind the pattern."
            )

    ptype = (pattern_type or "").strip().lower() or None
    conf = (confidence or "").strip().lower() or None

    if lvl == "inductive":
        if ptype not in PATTERN_TYPES:
            raise ObservationRejected(
                f"Rejected: an 'inductive' observation requires `pattern_type` — "
                f"one of: {', '.join(PATTERN_TYPES)}."
            )
        if conf not in CONFIDENCE_LEVELS:
            raise ObservationRejected(
                "Rejected: an 'inductive' observation requires `confidence` — "
                "'high' for 5+ sources, 'medium' for 3-4, 'low' for 2."
            )
    else:
        # Qualifiers are meaningless off the inductive level; drop rather than
        # reject, so a model that over-fills the schema still gets its fact in.
        ptype = conf = None

    # ── What it is about, and when it was true ───────────────────────────────
    subj = normalize_subject(subject)

    dom = (domain or "").strip().lower()
    if dom not in DOMAINS:
        raise ObservationRejected(
            f"Rejected: unknown domain '{domain}'. Use one of: {', '.join(DOMAINS)}. "
            f"Pick by what the fact IS, not where you want it filed — biography "
            f"(who someone is), preference (what he likes/wants and how), state "
            f"(true of his life now), project, training, finance, event "
            f"(a dated thing that happened)."
        )

    v_from = _parse_day(valid_from, "valid_from")
    v_until = _parse_day(valid_until, "valid_until")
    if v_from and v_until and v_until < v_from:
        raise ObservationRejected(
            f"Rejected: valid_until ({v_until}) is before valid_from ({v_from})."
        )

    # A biographical constant that has "ended" is a category error: the owner's
    # past does not stop having happened. If it stopped applying, it was a state.
    if dom == "biography" and v_until is not None:
        raise ObservationRejected(
            "Rejected: a 'biography' fact cannot have a `valid_until` — the past "
            "does not expire, our record of it only sharpens. If this describes "
            "something that STOPPED being true, it was a 'state', not biography; "
            "record it as domain='state' with the end date."
        )

    return {
        "content": text,
        "level": lvl,
        "subject": subj,
        "domain": dom,
        "valid_from": v_from,
        "valid_until": v_until,
        "source_ids": ids,
        "premises": prem,
        "sources": srcs,
        "pattern_type": ptype,
        "confidence": conf,
    }


# ── Write path ────────────────────────────────────────────────────────────────

async def _embed_content(text: str) -> bytes | None:
    """Best-effort embedding. Never raises: an unembedded observation is still a
    recorded one, and embed_pending_observations() heals it on the next pass."""
    from app.services.embeddings import embed_texts

    try:
        vec = (await embed_texts([text]))[0]
        return vec.tobytes()
    except Exception as e:  # noqa: BLE001
        logger.warning("observation_embed_failed", extra={"error": str(e)})
        return None


async def _converge_subject(db: AsyncSession, user_id: int, subject: str) -> str:
    """Reuse the existing spelling of a subject that differs only in case.

    The first spelling of a name wins and everything after it converges on that,
    so the roster cannot end up with three sections for one person just because
    three agents capitalised differently. Only applies to named subjects — the
    owner is a constant.
    """
    if subject == "owner" or ":" not in subject:
        return subject
    kind, _, name = subject.partition(":")
    rows = (
        await db.execute(
            select(Observation.subject)
            .where(
                Observation.user_id == user_id,
                Observation.subject.startswith(f"{kind}:"),
            )
            .distinct()
        )
    ).scalars().all()
    for existing in rows:
        if existing.lower() == subject.lower():
            return existing
    return subject


async def supersede(
    db: AsyncSession,
    *,
    user_id: int,
    old_id: int,
    new_id: int,
    ended: "date | None" = None,
) -> bool:
    """Close out an observation that a newer one replaces (v3 §3.2).

    Sets `valid_until` and `superseded_by` on the old row rather than editing or
    deleting it, so the previous value stays answerable and the correction stays
    reversible. Returns False if either row is missing.
    """
    from datetime import date as _date

    old = (
        await db.execute(
            _live(user_id).where(Observation.id == old_id)
        )
    ).scalar_one_or_none()
    new = (
        await db.execute(
            _live(user_id).where(Observation.id == new_id)
        )
    ).scalar_one_or_none()
    if old is None or new is None or old.id == new.id:
        return False

    old.valid_until = ended or new.valid_from or _date.today()
    old.superseded_by = new.id
    old.updated_at = datetime.now(timezone.utc)
    await db.commit()
    logger.info(
        "observation_superseded",
        extra={"old_id": old.id, "new_id": new.id, "ended": str(old.valid_until)},
    )
    return True


async def record_observations(
    db: AsyncSession,
    *,
    user_id: int,
    observer: str,
    proposals: list[dict],
    session_id: int | None = None,
    message_ids: list | None = None,
    request_id: str = "",
    origin: str = "live",
) -> tuple[list[Observation], list[str]]:
    """
    Validate and persist a batch of observations.

    Returns (stored, rejections). A batch is NOT all-or-nothing: valid members
    land even when siblings are rejected, and the rejection strings go back to
    the model so it can re-file the failures. Losing four good facts because the
    fifth lacked a premise would be the worse failure.

    A duplicate (same normalized content, observer and level, still live) is not
    inserted again — it reinforces the existing row. Repetition becomes rank.
    """
    stored: list[Observation] = []
    rejections: list[str] = []

    for proposal in proposals:
        try:
            clean = validate_observation(
                content=proposal.get("content", ""),
                level=proposal.get("level", "explicit"),
                subject=proposal.get("subject", "owner"),
                domain=proposal.get("domain", "state"),
                valid_from=proposal.get("valid_from"),
                valid_until=proposal.get("valid_until"),
                source_ids=proposal.get("source_ids"),
                premises=proposal.get("premises"),
                sources=proposal.get("sources"),
                pattern_type=proposal.get("pattern_type"),
                confidence=proposal.get("confidence"),
            )
        except ObservationRejected as e:
            rejections.append(str(e))
            continue

        clean["subject"] = await _converge_subject(db, user_id, clean["subject"])

        existing = (
            await db.execute(
                select(Observation).where(
                    Observation.user_id == user_id,
                    Observation.observer == observer,
                    Observation.level == clean["level"],
                    Observation.subject == clean["subject"],
                    Observation.content == clean["content"],
                    Observation.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()

        if existing is not None:
            existing.reinforcement_count += 1
            existing.updated_at = datetime.now(timezone.utc)
            if session_id is not None:
                existing.session_id = session_id
            if message_ids:
                merged = list(existing.message_ids or []) + list(message_ids)
                # Keep the tail: recent provenance is what recall pulls context from.
                existing.message_ids = merged[-20:]
            stored.append(existing)
            continue

        obs = Observation(
            user_id=user_id,
            observer=observer,
            content=clean["content"],
            level=clean["level"],
            subject=clean["subject"],
            domain=clean["domain"],
            valid_from=clean["valid_from"],
            valid_until=clean["valid_until"],
            source_ids=clean["source_ids"],
            premises=clean["premises"],
            sources=clean["sources"],
            pattern_type=clean["pattern_type"],
            confidence=clean["confidence"],
            session_id=session_id,
            message_ids=list(message_ids or []),
            request_id=request_id,
            origin=origin,
            embedding=await _embed_content(clean["content"]),
        )
        db.add(obs)
        stored.append(obs)

    if stored:
        await db.commit()
        for obs in stored:
            await db.refresh(obs)
        # Lexical index AFTER the commit, because it is keyed by the row id the
        # commit assigns. It is also a second commit rather than part of the
        # first: an FTS5 failure must not roll back a recorded fact, and the
        # index can always be rebuilt from the observations table.
        from app.services import lexical

        for obs in stored:
            await lexical.index_observation(db, obs)
        await db.commit()

    logger.info(
        "observations_recorded",
        extra={
            "request_id": request_id,
            "observer": observer,
            "stored": len(stored),
            "rejected": len(rejections),
        },
    )
    return stored, rejections


async def soft_delete_observations(
    db: AsyncSession, *, user_id: int, observation_ids: list[int], request_id: str = ""
) -> int:
    """Demote observations out of recall without destroying them (§3.4)."""
    if not observation_ids:
        return 0
    rows = (
        await db.execute(
            select(Observation).where(
                Observation.user_id == user_id,
                Observation.id.in_(observation_ids),
                Observation.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    now = datetime.now(timezone.utc)
    for obs in rows:
        obs.deleted_at = now
    if rows:
        await db.commit()
    logger.info(
        "observations_demoted",
        extra={"request_id": request_id, "count": len(rows)},
    )
    return len(rows)


# ── Routing: which surface a fact renders into (v3 §4) ───────────────────────

def target_file(obs: Observation) -> str:
    """The single surface this observation appears on.

    **This function is total.** Every live observation renders somewhere; there
    is no path that returns nothing. A fact that rendered nowhere would be
    invisible to the owner while still counting as recorded, which is precisely
    the "silently lost" failure the whole v3 design exists to make impossible —
    so the last line is an unconditional fallback, not a default that happens to
    catch the common case.

    Order matters. Validity is checked FIRST: anything that has stopped being
    true belongs in history.md regardless of what it is about, because that file
    answers "what used to be so" for every subject at once.
    """
    if obs.valid_until is not None:
        return "/memories/history.md"
    if obs.subject.startswith("person:"):
        return "/memories/social.md"
    if obs.subject.startswith("project:"):
        return "/memories/projects.md"
    if obs.domain == "preference":
        return "/memories/dossier.md"
    if obs.domain == "training":
        return "/memories/sessions.md"
    if obs.domain == "finance":
        return "/memories/finance.md"
    if obs.domain == "biography":
        return "/memories/owner.md"
    return "/memories/current.md"


# ── Read path ─────────────────────────────────────────────────────────────────

# ── The vector cache ─────────────────────────────────────────────────────────
# Recall used to be a tool call — occasional, and paying 12 MB of BLOB reads for
# one was nobody's problem. Since app/services/relevant_recall.py runs a search
# on EVERY turn, it is: 2,000 live observations carry 12.3 MB of embeddings, and
# loading them measured 227 ms before a single dot product had been computed.
# That is 227 ms added to every message the owner sends, to re-read bytes that
# change a few times a day.
#
# So the vectors are held in process, keyed on a watermark cheap enough to check
# every time (one aggregate row: how many live observations there are and when
# one last changed). The FILTERING still happens in SQL, which is what keeps
# this a pure optimisation — every level/subject/domain/validity predicate is
# evaluated by the database exactly as before, and the cache is consulted only
# to turn the surviving ids into vectors. The row query then defers the
# `embedding` column, so the BLOBs are not read at all on a cache hit.
#
# A miss costs what the old path cost every time. An id the cache has not seen
# (embedded by a background task since the watermark was taken) simply does not
# take part in the vector pass that turn, and the lexical half still ranks it —
# which is the same graceful degradation the two-retriever design already has
# for a row the embedder has not reached yet.

_VECTOR_CACHE: dict[int, tuple[tuple, dict[int, "np.ndarray"]]] = {}


async def _vector_watermark(db: AsyncSession, user_id: int) -> tuple:
    """A cheap fingerprint of this owner's live embedded rows.

    Count plus the newest `updated_at` plus the highest id: between them these
    move on every insert, every re-embed and every soft delete, and the whole
    thing is one indexed aggregate rather than a scan.
    """
    from sqlalchemy import func

    row = (
        await db.execute(
            select(
                func.count(Observation.id),
                func.max(Observation.updated_at),
                func.max(Observation.id),
            ).where(
                Observation.user_id == user_id,
                Observation.deleted_at.is_(None),
                Observation.embedding.is_not(None),
            )
        )
    ).one()
    return (row[0], str(row[1]), row[2])


async def vectors_for(db: AsyncSession, user_id: int) -> dict[int, "np.ndarray"]:
    """`{observation_id: unit vector}` for this owner, cached in process."""
    watermark = await _vector_watermark(db, user_id)
    cached = _VECTOR_CACHE.get(user_id)
    if cached is not None and cached[0] == watermark:
        return cached[1]

    rows = (
        await db.execute(
            select(Observation.id, Observation.embedding).where(
                Observation.user_id == user_id,
                Observation.deleted_at.is_(None),
                Observation.embedding.is_not(None),
            )
        )
    ).all()
    vectors = {
        int(oid): np.frombuffer(blob, dtype=np.float32)
        for oid, blob in rows
        if blob
    }
    _VECTOR_CACHE[user_id] = (watermark, vectors)
    logger.info(
        "observation_vector_cache_rebuilt",
        extra={"user_id": user_id, "vectors": len(vectors)},
    )
    return vectors


def invalidate_vector_cache(user_id: int | None = None) -> None:
    """Drop the cached vectors. The watermark makes this belt-and-braces rather
    than load-bearing, but a writer that knows it changed the store should say
    so instead of relying on a fingerprint to notice."""
    if user_id is None:
        _VECTOR_CACHE.clear()
    else:
        _VECTOR_CACHE.pop(user_id, None)


def _live(user_id: int):
    return (
        select(Observation)
        .where(Observation.user_id == user_id, Observation.deleted_at.is_(None))
    )


async def search_observations(
    db: AsyncSession,
    *,
    user_id: int,
    query: str,
    limit: int = 15,
    level: str | None = None,
    observer: str | None = None,
    subject: str | None = None,
    domain: str | None = None,
    as_of=None,
    live_only: bool = False,
    after: datetime | None = None,
    before: datetime | None = None,
) -> list[tuple[Observation, float]]:
    """
    HYBRID search over observations, newest-biased and optionally filtered.

    Two retrievers run over the same filtered candidate set and their rankings
    are fused with Reciprocal Rank Fusion (app/services/lexical.py):

      * **Meaning** — a plain dot product over L2-normalized vectors, the same
        brute-force approach app/services/embeddings.py justifies for messages.
        Honcho reaches for pgvector HNSW here; at this store's scale (SQLite in
        production, one owner, thousands of observations rather than millions) an
        index costs more complexity than it buys latency, and it would not
        survive the SQLite/Postgres split this project runs across.
      * **Words** — BM25 over an FTS5 index. This is the half that was missing,
        and its absence is what made recall unreliable on exactly the things
        worth recalling by name: a course code, a person, an amount, a Turkish
        word the embedding model has quietly averaged into its neighbours.

    Either half may come back empty — no embeddings yet, no FTS5 on this build,
    a query of pure stopwords — and fusion of one list with nothing is that
    list. So this degrades to whichever retriever is available rather than
    failing.

    **The relevance floor.** RRF keeps order and discards magnitude, and that is
    a real hole: the rank-1 row of a list of pure noise fuses to the same 1.0 as
    a perfect match, so recall could not distinguish "here is the fact" from
    "here is the closest of 638 things, none of which is it". Measured against
    evals/recall, 71% of probes came back with a wrong rank-1 at score ≥ 0.90.
    So the vector half is now floored BEFORE fusion: a candidate under
    `settings.recall_min_similarity` never enters the ranking. Nothing above the
    floor means an empty result, and an empty result is the honest answer — it
    is what lets the caller say "not in memory, ask him" instead of confidently
    reporting the nearest unrelated row.

    The lexical half needs no floor: FTS5 only returns rows that actually
    contain a query term, so its candidate set is already relevance-gated by
    construction.

    Returns (observation, score) pairs, highest first, where the score is the
    normalised RRF agreement between the two rankings (1.0 = ranked first by
    both), NOT a cosine similarity.
    """
    from app.config import settings
    from app.services import lexical, query_translation
    from app.services.embeddings import embed_texts

    from sqlalchemy import or_

    # Cross-lingual step, in front of BOTH retrievers. A Turkish question cannot
    # reach an English store on its own: the vector half degrades and the
    # lexical half matches nothing at all. `expand` gives the meaning pass the
    # English form and the keyword pass both forms, so the Turkish proper nouns
    # the store legitimately keeps are still matchable. An English query, a
    # disabled setting or a failed call all return the query untouched.
    vector_query, lexical_query = await query_translation.expand(query)

    stmt = _live(user_id)
    if level:
        stmt = stmt.where(Observation.level == level)
    if observer:
        stmt = stmt.where(Observation.observer == observer)
    if subject:
        stmt = stmt.where(Observation.subject == normalize_subject(subject))
    if domain:
        stmt = stmt.where(Observation.domain == domain)
    if live_only:
        # "Has not ended YET" — not "has no end date". A fact can carry a FUTURE
        # end date and still be true today: his job runs through 2026-09-25 and
        # his gym membership expires 2026-09-05, and both are current until they
        # are not. Filtering on `valid_until IS NULL` excluded exactly the facts
        # that are most precisely dated, which is the opposite of the intent —
        # and it went unnoticed while almost nothing in the store had an end
        # date at all.
        today = datetime.now(timezone.utc).date()
        stmt = stmt.where(
            or_(Observation.valid_until.is_(None), Observation.valid_until > today)
        )
    if as_of is not None:
        # What was true ON that day: started by then, and had not yet ended.
        # This is the query that replaces reading a demoted file to work out
        # when a position changed.
        day = _parse_day(as_of, "as_of")
        stmt = stmt.where(
            or_(Observation.valid_from.is_(None), Observation.valid_from <= day),
            or_(Observation.valid_until.is_(None), Observation.valid_until > day),
        )
    if after:
        stmt = stmt.where(Observation.created_at >= after)
    if before:
        stmt = stmt.where(Observation.created_at < before)
    stmt = stmt.order_by(Observation.created_at.desc()).limit(MAX_SEARCH_CANDIDATES)
    # The embedding column is never read off these rows — vectors come from
    # vectors_for()'s cache — and it is 6 KB per row, so deferring it is the
    # difference between a 12 MB candidate load and a metadata one.
    stmt = stmt.options(defer(Observation.embedding))

    rows = list((await db.execute(stmt)).scalars().all())
    if not rows:
        return []

    by_id = {o.id: o for o in rows}

    # ── Words ────────────────────────────────────────────────────────────────
    # Runs first and unconditionally: it needs no network, so a lexical hit
    # survives an embedding provider that is down, rate-limited or unconfigured.
    lexical_ids = await lexical.search(
        db, query=lexical_query, limit=limit * 4, allowed_ids=set(by_id)
    )

    # ── Meaning ──────────────────────────────────────────────────────────────
    vector_ids: list[int] = []
    floor = float(settings.recall_min_similarity)
    vectors = await vectors_for(db, user_id)
    # Only the candidates that survived the SQL filters, in a stable order, so
    # the matrix rows line up with `embedded` for the argsort below.
    embedded = [o for o in rows if o.id in vectors]
    if embedded:
        try:
            query_vec = (await embed_texts([vector_query]))[0]
            matrix = np.stack([vectors[o.id] for o in embedded])
            scores = matrix @ query_vec
            # Ordered best-first, then cut at the floor. Cutting AFTER the sort
            # rather than masking before it keeps this a single pass and leaves
            # the floor trivially observable in the log line below.
            ordered = np.argsort(-scores)[: limit * 4]
            vector_ids = [
                embedded[int(i)].id for i in ordered if float(scores[i]) >= floor
            ]
            if not vector_ids and len(ordered):
                logger.info(
                    "observation_vector_pass_below_floor",
                    extra={
                        "query": query[:120],
                        "best": round(float(scores[ordered[0]]), 3),
                        "floor": floor,
                    },
                )
        except Exception as e:  # noqa: BLE001
            # A failed embedding call is no longer fatal to recall — there is a
            # second retriever now, and answering from words alone beats
            # answering "memory search is unavailable".
            logger.warning("observation_vector_pass_failed", extra={"error": str(e)})

    if not lexical_ids and not vector_ids:
        # Neither retriever produced anything: no term matched, and nothing
        # cleared the similarity floor. This used to fall back to newest-first,
        # which was the single worst behaviour in recall — it answered every
        # unanswerable question with whatever happened to be recent, and the
        # caller had no way to tell that from a real hit. Returning nothing is
        # the honest answer, and the tool layer renders it as "not in memory".
        return []

    fused = lexical.rrf_fuse(vector_ids, lexical_ids, limit=limit)
    return [(by_id[i], score) for i, score in fused if i in by_id]


async def recent_observations(
    db: AsyncSession, *, user_id: int, limit: int = 15, observer: str | None = None
) -> list[Observation]:
    stmt = _live(user_id)
    if observer:
        stmt = stmt.where(Observation.observer == observer)
    stmt = stmt.order_by(Observation.created_at.desc()).limit(limit)
    return list((await db.execute(stmt)).scalars().all())


async def most_reinforced_observations(
    db: AsyncSession, *, user_id: int, limit: int = 15
) -> list[Observation]:
    """The owner's most established facts — what several conversations agree on.
    Honcho's `get_most_derived_observations`, ranked by reinforcement."""
    stmt = (
        _live(user_id)
        .order_by(Observation.reinforcement_count.desc(), Observation.updated_at.desc())
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())


async def reasoning_chain(
    db: AsyncSession, *, user_id: int, observation_id: int, direction: str = "both"
) -> dict:
    """
    Walk one step of the derivation graph around an observation.

    `premises` returns what it rests on (its source_ids); `conclusions` returns
    what rests on IT (rows citing it). This is what turns "he is cutting weight"
    from an assertion into an argument — and what lets a wrong premise be traced
    to everything it poisoned before any of it is acted on.
    """
    root = (
        await db.execute(
            _live(user_id).where(Observation.id == observation_id)
        )
    ).scalar_one_or_none()
    if root is None:
        return {"root": None, "premises": [], "conclusions": []}

    premises: list[Observation] = []
    if direction in ("premises", "both") and root.source_ids:
        ids = [int(i) for i in root.source_ids]
        premises = list(
            (await db.execute(_live(user_id).where(Observation.id.in_(ids))))
            .scalars()
            .all()
        )

    conclusions: list[Observation] = []
    if direction in ("conclusions", "both"):
        # source_ids is a JSON list; a portable containment test across SQLite and
        # Postgres means filtering in Python. The candidate set is bounded to
        # derived rows only, which is a small fraction of the store.
        derived = list(
            (
                await db.execute(
                    _live(user_id).where(Observation.level != "explicit")
                )
            )
            .scalars()
            .all()
        )
        conclusions = [
            o for o in derived if observation_id in [int(i) for i in (o.source_ids or [])]
        ]

    return {"root": root, "premises": premises, "conclusions": conclusions}


# ── Self-healing ──────────────────────────────────────────────────────────────

async def embed_pending_observations(user_id: int, request_id: str = "") -> int:
    """
    Embed observations stored while the embedding provider was unavailable.

    Same self-healing contract as embedding_indexer.backfill_embeddings: always
    processes whatever is pending, so it is safe to re-run at any time. Opens its
    own DB session — it runs off the request path.
    """
    from app.database import AsyncSessionLocal
    from app.services.embeddings import embed_texts

    stored = 0
    try:
        async with AsyncSessionLocal() as db:
            rows = list(
                (
                    await db.execute(
                        _live(user_id)
                        .where(Observation.embedding.is_(None))
                        .order_by(Observation.created_at.asc())
                        .limit(256)
                    )
                )
                .scalars()
                .all()
            )
            if not rows:
                return 0
            # One batched call, then one commit — the DB session is not held open
            # across the network hop's full duration by accident: the call is the
            # only await between the read and the write, and both are cheap.
            vectors = await embed_texts([o.content for o in rows])
            for obs, vec in zip(rows, vectors):
                obs.embedding = vec.tobytes()
                stored += 1
            await db.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "observation_backfill_failed",
            extra={"request_id": request_id, "error": str(e)},
        )
        return stored

    logger.info(
        "observations_embedded",
        extra={"request_id": request_id, "stored": stored},
    )
    return stored


async def index_pending_observations(user_id: int, request_id: str = "") -> int:
    """
    Put every not-yet-indexed observation into the lexical index.

    The keyword half of recall arrived after the record did, so most of what is
    already stored has no FTS5 row — and unlike the embedding backfill, which
    can find its gaps with `embedding IS NULL`, the gap here lives in a table the
    ORM does not model. So the query is inverted: ask FTS5 which rowids it
    already holds, and index the rest.

    Same self-healing contract as embed_pending_observations: bounded per call,
    idempotent, safe to re-run, opens its own session because it runs off the
    request path.
    """
    from app.database import AsyncSessionLocal
    from app.services import lexical

    indexed = 0
    try:
        async with AsyncSessionLocal() as db:
            if not await lexical.ensure_index(db):
                return 0
            known = await lexical.indexed_ids(db)
            rows = list(
                (await db.execute(
                    _live(user_id).order_by(Observation.created_at.desc()).limit(5000)
                )).scalars().all()
            )
            missing = [o for o in rows if o.id not in known][:512]
            if not missing:
                return 0
            indexed = await lexical.rebuild(db, missing)
            await db.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "observation_index_backfill_failed",
            extra={"request_id": request_id, "error": str(e)},
        )
        return indexed

    logger.info(
        "observations_indexed",
        extra={"request_id": request_id, "indexed": indexed},
    )
    return indexed


# ── Formatting for tool results ───────────────────────────────────────────────

def format_observation(obs: Observation, *, score: float | None = None) -> str:
    """
    One observation rendered for a model to reason over.

    The id is rendered as `[id:N]` because that is what the model must pass back
    as a `source_id` when it derives something from this fact — Honcho's format,
    kept because it survives the round trip through every provider's tokenizer
    without being mistaken for prose.
    """
    bits = [f"[id:{obs.id}]", f"({obs.level}"]
    if obs.pattern_type:
        bits[-1] += f"/{obs.pattern_type}"
    if obs.confidence:
        bits[-1] += f", {obs.confidence} confidence"
    bits[-1] += ")"
    head = " ".join(bits)

    meta = [obs.subject, obs.domain, obs.observer, obs.created_at.strftime("%Y-%m-%d")]
    # Validity is shown only when it is not "still true" — the common case needs
    # no annotation, and a superseded fact must never read as current.
    if obs.valid_until is not None:
        span = f"until {obs.valid_until}"
        if obs.valid_from:
            span = f"{obs.valid_from} → {obs.valid_until}"
        meta.append(f"ENDED ({span})")
        if obs.superseded_by:
            meta.append(f"replaced by id:{obs.superseded_by}")
    elif obs.valid_from:
        meta.append(f"since {obs.valid_from}")
    if obs.reinforcement_count > 1:
        meta.append(f"seen {obs.reinforcement_count}×")
    if score is not None:
        meta.append(f"score {score:.2f}")

    line = f"{head} {obs.content}\n    — {' · '.join(meta)}"
    if obs.source_ids:
        line += f"\n    derived from: {', '.join(f'id:{i}' for i in obs.source_ids)}"
    return line


def format_observations(
    scored: list[tuple[Observation, float]] | list[Observation], header: str
) -> str:
    if not scored:
        return f"{header}: nothing recorded yet."
    lines = [f"{header} ({len(scored)}):", ""]
    for item in scored:
        if isinstance(item, tuple):
            lines.append(format_observation(item[0], score=item[1]))
        else:
            lines.append(format_observation(item))
    return "\n".join(lines)
