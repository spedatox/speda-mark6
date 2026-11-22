"""Neural multilingual episodic cortex: dense ONNX embeddings + char n-grams + temporal continuity.

Replaces hardcoded keyword concept buckets with genuine local multilingual dense
vectors (`intfloat/multilingual-e5-small`) combined with lexical overlap, character
n-gram typo resilience, and smooth temporal decay.
"""
from __future__ import annotations

from collections import Counter, OrderedDict
from datetime import datetime, time, timedelta, timezone
import math
import os
import re
import threading

import numpy as np

from app.services.lexical import fold

# Clean, general domain descriptions embedded once by the neural model for
# zero-regex tool preloading & semantic domain routing (no hardcoded personal names/places).
_DOMAIN_PROTOTYPES: dict[str, str] = {
    "travel_mobility": "travel, trip, bus, flight, train, ticket, departure, arrival, commute, route, distance, traffic, destination, heading somewhere",
    "home_family_location": "home, family, city, dorm, address, location, visiting family, staying home, weekend away",
    "academic_exam_grade": "university exam, midterm, final, quiz, grade, score, course, lecture, study, professor, syllabus, class",
    "schedule_time_commitment": "calendar, schedule, plan, appointment, meeting, deadline, reminder, rescheduling, time change, overlap, conflict",
    "personal_emotional_social": "feelings, relationships, friends, family emotions, tired, lonely, personal conversation, venting",
    "health_wellbeing": "health, gym, workout, exercise, sleep, medication, nutrition, steps, wellbeing",
    "system_memory_infra": "software engineering, memory retrieval, database, server, logs, debugging, deployment, container, timeout",
    "security_network": "cybersecurity, firewall, ssh, network attack, intrusion, watchdog, vulnerability, security audit",
}

_RELIEF_OR_OPENING_CUE = re.compile(
    r"^\s*(?:phew+|whew+|uff+|of+|oh\s+man|oh\s+boy|thank\s+god|finally|well\.{2,}|[a-z_]+\.{3,}|"
    r"done|finished|over|survived|free\s+as\s+a\s+bird)[.!?\s]*$",
    re.I,
)

_TEMPORAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("last_night", re.compile(r"\b(last\s+night|yesterday\s+evening|yesterday\s+night|dun\s+gece|dun\s+aksam)\b")),
    ("yesterday", re.compile(r"\b(yesterday|dun)\b")),
    ("this_morning", re.compile(r"\b(this\s+morning|earlier\s+today|bu\s+sabah|bugun\s+sabah)\b")),
    ("recent_talk", re.compile(
        r"\b(our\s+(?:last\s+)?(?:conversation|chat|talk|discussion)|"
        r"rememb\w*\s+(?:our|what\s+we)|remeb\w*\s+(?:our|what\s+we)|"
        r"konustugumuz|sohbetimiz|gecen\s+konusma\w*)\b"
    )),
)

_VEC_CACHE_LOCK = threading.Lock()
_QUERY_VEC_CACHE: OrderedDict[str, np.ndarray] = OrderedDict()
_PASSAGE_VEC_CACHE: OrderedDict[str, np.ndarray] = OrderedDict()
_PROTOTYPE_MATRIX: tuple[list[str], np.ndarray] | None = None


def _semantic_enabled() -> bool:
    return os.getenv("SPEDA_SEMANTIC_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}


def _cached_query_vec(text: str) -> np.ndarray | None:
    if not _semantic_enabled() or not (text or "").strip():
        return None
    key = text[:1500]
    with _VEC_CACHE_LOCK:
        cached = _QUERY_VEC_CACHE.get(key)
        if cached is not None:
            _QUERY_VEC_CACHE.move_to_end(key)
            return cached
    try:
        from app.services.semantic_index import NeuralEmbedder
        vec = NeuralEmbedder.shared().embed_query_sync(key)
    except Exception:
        return None
    with _VEC_CACHE_LOCK:
        _QUERY_VEC_CACHE[key] = vec
        while len(_QUERY_VEC_CACHE) > 256:
            _QUERY_VEC_CACHE.popitem(last=False)
    return vec


def _cached_passage_vec(text: str) -> np.ndarray | None:
    if not _semantic_enabled() or not (text or "").strip():
        return None
    key = text[:1500]
    with _VEC_CACHE_LOCK:
        cached = _PASSAGE_VEC_CACHE.get(key)
        if cached is not None:
            _PASSAGE_VEC_CACHE.move_to_end(key)
            return cached
    try:
        from app.services.semantic_index import NeuralEmbedder
        vec = NeuralEmbedder.shared().embed_passages_sync([key])[0]
    except Exception:
        return None
    with _VEC_CACHE_LOCK:
        _PASSAGE_VEC_CACHE[key] = vec
        while len(_PASSAGE_VEC_CACHE) > 512:
            _PASSAGE_VEC_CACHE.popitem(last=False)
    return vec


def _prototype_matrix() -> tuple[list[str], np.ndarray] | None:
    global _PROTOTYPE_MATRIX
    if _PROTOTYPE_MATRIX is not None:
        return _PROTOTYPE_MATRIX
    if not _semantic_enabled():
        return None
    try:
        from app.services.semantic_index import NeuralEmbedder
        names = list(_DOMAIN_PROTOTYPES.keys())
        mat = NeuralEmbedder.shared().embed_passages_sync([_DOMAIN_PROTOTYPES[n] for n in names])
        _PROTOTYPE_MATRIX = (names, mat)
        return _PROTOTYPE_MATRIX
    except Exception:
        return None


def _normalize_token(token: str) -> str:
    """Collapse repeated trailing characters ('phewwww' -> 'phew', 'noooo' -> 'no')."""
    if len(token) > 3:
        token = re.sub(r"([a-z])\1{2,}$", r"\1", token)
    return token


def char_ngrams(text: str, n_values: tuple[int, ...] = (3, 4)) -> Counter[str]:
    """Build character n-gram frequency vector resilient to typos and suffixes."""
    cleaned = re.sub(r"[^a-z0-9]+", " ", fold(text)).strip()
    if not cleaned:
        return Counter()
    counts: Counter[str] = Counter()
    for word in cleaned.split():
        norm = _normalize_token(word)
        if len(norm) < 2:
            continue
        padded = f"^{norm}$"
        for n in n_values:
            if len(padded) >= n:
                for i in range(len(padded) - n + 1):
                    counts[padded[i:i + n]] += 1
    return counts


def cosine_counter(a: Counter[str], b: Counter[str]) -> float:
    if not a or not b:
        return 0.0
    common = set(a.keys()) & set(b.keys())
    if not common:
        return 0.0
    dot = sum(a[k] * b[k] for k in common)
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def concept_vector(text: str) -> Counter[str]:
    """Project text onto general domain prototypes using neural multilingual cosine similarity."""
    vec: Counter[str] = Counter()
    if not (text or "").strip():
        return vec
    q_vec = _cached_query_vec(text)
    proto = _prototype_matrix()
    if q_vec is None or proto is None:
        return vec
    names, mat = proto
    sims = mat @ q_vec
    top_sim = float(np.max(sims)) if sims.size else 0.0
    threshold = max(0.22, top_sim - 0.24)
    for name, sim in zip(names, sims):
        sc = float(sim)
        if sc >= threshold:
            vec[name] = max(1, int(round(sc * 100)))
    return vec


def hybrid_episode_score(
    query: str,
    query_terms_list: list[str],
    episode_text: str,
    *,
    query_ngrams: Counter[str] | None = None,
    query_concepts: Counter[str] | None = None,
    idf: dict[str, float] | None = None,
    age_hours: float = 0.0,
) -> float:
    """Compute genuine neural semantic + lexical + char-ngram + temporal episode score."""
    folded_ep = fold(episode_text)
    if not folded_ep.strip():
        return 0.0
    ep_words = set(re.findall(r"\w+", folded_ep))
    ep_norm_words = ep_words | {_normalize_token(w) for w in ep_words}

    # 1. Lexical term match
    lexical_score = 0.0
    for term in query_terms_list:
        norm_t = _normalize_token(term)
        weight = (idf or {}).get(term, min(len(term), 12) * 1.5)
        if term in ep_words or norm_t in ep_norm_words:
            lexical_score += weight * 3.0
        elif term in folded_ep or (len(norm_t) >= 4 and norm_t in folded_ep):
            lexical_score += weight * 1.8

    # 2. Character n-gram cosine similarity (resilient to typos like "remebet" / "satursay")
    q_ng = query_ngrams if query_ngrams is not None else char_ngrams(query)
    ep_ng = char_ngrams(folded_ep[:2400])
    ngram_sim = cosine_counter(q_ng, ep_ng)

    # 3. Genuine multilingual neural dense cosine similarity
    neural_sim = 0.0
    q_vec = _cached_query_vec(query)
    ep_vec = _cached_passage_vec(episode_text[:1500]) if q_vec is not None else None
    if q_vec is not None and ep_vec is not None:
        neural_sim = float(np.dot(q_vec, ep_vec))

    # 4. Smooth temporal decay bonus for recent 72h episodes
    recency_bonus = max(0.0, 12.0 * math.exp(-max(0.0, age_hours) / 36.0)) if (
        lexical_score > 0 or ngram_sim >= 0.14 or neural_sim >= 0.35
    ) else 0.0

    total = (
        lexical_score
        + (28.0 * ngram_sim if ngram_sim >= 0.10 else 0.0)
        + (40.0 * neural_sim if neural_sim >= 0.32 else 0.0)
        + recency_bonus
    )
    return round(total, 4)


def detect_temporal_window(query: str, local_now: datetime) -> tuple[str | None, datetime | None, datetime | None]:
    """Resolve relative temporal expressions in owner-local time to UTC naive bounds."""
    folded = fold(query)
    tz = local_now.tzinfo or timezone.utc
    for kind, pattern in _TEMPORAL_PATTERNS:
        if pattern.search(folded):
            if kind == "last_night":
                prev_day = local_now.date() - timedelta(days=1)
                start_local = datetime.combine(prev_day, time(17, 0), tzinfo=tz)
                end_local = datetime.combine(local_now.date(), time(6, 0), tzinfo=tz)
                return (
                    kind,
                    start_local.astimezone(timezone.utc).replace(tzinfo=None),
                    end_local.astimezone(timezone.utc).replace(tzinfo=None),
                )
            if kind == "yesterday":
                prev_day = local_now.date() - timedelta(days=1)
                start_local = datetime.combine(prev_day, time(0, 0), tzinfo=tz)
                end_local = datetime.combine(local_now.date(), time(4, 0), tzinfo=tz)
                return (
                    kind,
                    start_local.astimezone(timezone.utc).replace(tzinfo=None),
                    end_local.astimezone(timezone.utc).replace(tzinfo=None),
                )
            if kind == "this_morning":
                start_local = datetime.combine(local_now.date(), time(4, 0), tzinfo=tz)
                end_local = datetime.combine(local_now.date(), time(14, 0), tzinfo=tz)
                return (
                    kind,
                    start_local.astimezone(timezone.utc).replace(tzinfo=None),
                    end_local.astimezone(timezone.utc).replace(tzinfo=None),
                )
            if kind == "recent_talk":
                start_local = local_now - timedelta(hours=72)
                return (
                    kind,
                    start_local.astimezone(timezone.utc).replace(tzinfo=None),
                    local_now.astimezone(timezone.utc).replace(tzinfo=None),
                )
    return None, None, None


def detect_named_peer(text: str, profiles, current_agent_id: str):
    """Detect natural English or Turkish references to a conversation with another Mark VI persona."""
    if not profiles or not text:
        return None
    folded = fold(text.strip())
    patterns = (
        r"\b(?:(?:i|we)\s+(?:talked|spoke|chatted|discussed|vented|checked|worked)(?:\s+[\w'-]+){0,6}?\s+(?:with|to)\s+|"
        r"(?:my|our|last\s+night'?s|yesterday'?s)\s+(?:chat|conversation|talk|discussion|session|thread)\s+with\s+|"
        r"what\s+([a-z_]+)\s+(?:said|told\s+me|found|reported|suggested|recommended)|"
        r"remember\s+(?:talking|speaking|chatting)\s+(?:with|to)\s+)([a-z_]+)?\b",
        r"\b([a-z_]+)\s+(?:ile|['’]?(?:yla|yle|la|le))\s+(?:[\w'-]+\s+){0,5}?(?:konust\w*|sohbet\w*|gorust\w*|planlad\w*|bakt\w*)\b",
        r"\b([a-z_]+)['’]?(?:in|un|nin|nun)\s+(?:dedigi|soyledigi|raporu|sohbeti|konusmasi)\b",
    )
    for pat in patterns:
        for match in re.finditer(pat, folded):
            candidates = [g for g in match.groups() if g]
            for name in candidates:
                if name in profiles.profiles or name in getattr(profiles, "aliases", {}):
                    persona = profiles.require(name)
                    if persona.agent_id != current_agent_id:
                        return persona
    return None


def is_unanchored_or_implicit_opening(query: str, user_turn_count: int) -> bool:
    """True when the turn is an opening or early follow-up that relies on prior session continuity."""
    folded = fold(query.strip())
    if not folded:
        return True
    if _RELIEF_OR_OPENING_CUE.match(folded):
        return True
    words = re.findall(r"\w+", folded)
    if user_turn_count <= 2 and len(words) <= 6:
        return True
    return False
