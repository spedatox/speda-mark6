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
_INTENT_PROTOTYPES: dict[str, np.ndarray] | None = None

# Calibrated on multilingual (EN/TR/mixed/paraphrase) evaluation pairs against
# intfloat/multilingual-e5-small (see evaluation report):
# - Dated states: max_neg=0.316, min_pos=0.410 -> midpoint 0.36
CALIBRATED_DATED_STATE_THRESHOLD = 0.36
# - Direct owner-relation phrases: max_neg=0.338, min_pos=0.498 (clean phrases) -> floor 0.44, band 0.21
CALIBRATED_RELATION_FLOOR = 0.44
CALIBRATED_RELATION_BAND = 0.21
# - Travel disruption / schedule change (combined with travel_mobility domain)
CALIBRATED_JOURNEY_CHANGE_THRESHOLD = 0.45
# - Contrastive owner enrollment intent: min_pos=0.505, contrastive margin=0.02
CALIBRATED_ENROLLMENT_FLOOR = 0.48
CALIBRATED_CONTRAST_MARGIN = 0.02

_INTENT_TEXTS: dict[str, str] = {
    "enroll_current": "Which university, college, major, or degree program am I currently enrolled in or studying right now?",
    "enroll_past": "Where did I previously study, attend school in the past, or graduate from earlier?",
    "enroll_other": "Which university, college, or school does my sister, brother, friend, or another person attend?",
    "enroll_referent": "You are currently at your university, college, or school.",
    "enroll_referent_past": "That was your old, former, or previous school or college.",
    "enroll_referent_other": "Your sister, brother, or friend attends a university or school.",
    "enroll_institution": "Which university, college, or institution do I attend?",
    "enroll_program": "What is my academic major, degree program, department, or field of study?",
    "journey_change": "My bus, flight, train, or travel itinerary was changed, delayed, moved, rescheduled, booked, or confirmed.",
    "journey_non_travel": "My office meeting, call, or work appointment was rescheduled or moved.",
    "sec_institution": "Current university or college enrollment or academic placement of the owner",
    "sec_program": "Academic major, degree program, or department field of study",
    "sec_past": "Previously completed or former graduated degree or old school",
    "sec_student_id": "Student identification number or registration ID code",
}


def _intent_prototypes() -> dict[str, np.ndarray] | None:
    global _INTENT_PROTOTYPES
    if _INTENT_PROTOTYPES is not None:
        return _INTENT_PROTOTYPES
    if not _semantic_enabled():
        return None
    try:
        from app.services.semantic_index import NeuralEmbedder
        keys = list(_INTENT_TEXTS.keys())
        mat = NeuralEmbedder.shared().embed_passages_sync([_INTENT_TEXTS[k] for k in keys])
        _INTENT_PROTOTYPES = {keys[i]: mat[i] for i in range(len(keys))}
        return _INTENT_PROTOTYPES
    except Exception:
        return None


def classify_enrollment_intent(query: str) -> list[str]:
    """Contrastive multilingual neural classification for current owner enrollment queries.

    Returns a non-empty subset of ['institution', 'program'] when the query asks about
    the owner's own current university/program (rejecting past education and other people's
    schools without hardcoded keyword lists).
    """
    if not (query or "").strip():
        return []
    protos = _intent_prototypes()
    qv = _cached_query_vec(query)
    if protos is None or qv is None:
        return []
    s_cur = float(np.dot(qv, protos["enroll_current"]))
    s_past = float(np.dot(qv, protos["enroll_past"]))
    s_other = float(np.dot(qv, protos["enroll_other"]))
    matched = (
        s_cur >= CALIBRATED_ENROLLMENT_FLOOR
        and s_cur > s_past + CALIBRATED_CONTRAST_MARGIN
        and s_cur > s_other + CALIBRATED_CONTRAST_MARGIN
    )
    if not matched and "\n" in query:
        # Check borrowed referent line from a referential follow-up (e.g. 'What would the name be?').
        borrowed = query.partition("\n")[2].partition("\n")[0].strip()
        if borrowed:
            bv = _cached_query_vec(borrowed)
            if bv is not None:
                r_cur = float(np.dot(bv, protos["enroll_referent"]))
                r_past = float(np.dot(bv, protos["enroll_referent_past"]))
                r_other = float(np.dot(bv, protos["enroll_referent_other"]))
                if r_cur >= 0.44 and r_cur > r_past + 0.04 and r_cur > r_other + 0.04:
                    return ["institution"]
    if not matched:
        return []
    s_inst = float(np.dot(qv, protos["enroll_institution"]))
    s_prog = float(np.dot(qv, protos["enroll_program"]))
    # If both institution and program are strongly signaled, require both; otherwise pick the dominant one.
    fields: list[str] = []
    if s_inst >= s_prog - 0.08:
        fields.append("institution")
    if s_prog >= s_inst - 0.17 and s_prog >= 0.48:
        fields.append("program")
    return fields or ["institution"]


def is_changed_journey(query: str) -> bool:
    """Contrastive multilingual neural detection for travel itinerary changes/delays (zero regex verbs)."""
    if not (query or "").strip():
        return False
    protos = _intent_prototypes()
    qv = _cached_query_vec(query)
    if protos is None or qv is None:
        return False
    s_jc = float(np.dot(qv, protos["journey_change"]))
    s_non_travel = float(np.dot(qv, protos["journey_non_travel"]))
    return s_jc >= CALIBRATED_JOURNEY_CHANGE_THRESHOLD and s_jc > s_non_travel


def match_owner_relations(
    query: str,
    catalog: list[tuple[int, str, list[str], list[str]]],
    *,
    floor: float = CALIBRATED_RELATION_FLOOR,
    band: float = CALIBRATED_RELATION_BAND,
) -> set[int]:
    """Match a multilingual query against structurally extracted direct owner-relation phrases.

    Replaces hardcoded _RELATIONS dictionaries with general-purpose neural similarity so any
    relationship (family, advisor, roommate, dentist, colleague, etc.) works across languages.
    """
    if not (query or "").strip() or not catalog:
        return set()
    qv = _cached_query_vec(query)
    if qv is None:
        return set()
    scored: list[tuple[int, float]] = []
    for person_id, _title, _aliases, rel_phrases in catalog:
        if not rel_phrases:
            continue
        best = 0.0
        for phrase in rel_phrases:
            pv = _cached_passage_vec(phrase)
            if pv is not None:
                sim = float(np.dot(qv, pv))
                if sim > best:
                    best = sim
        if best >= floor:
            scored.append((person_id, best))
    if not scored:
        return set()
    top_sim = max(s for _, s in scored)
    cutoff = max(floor, top_sim - band)
    return {pid for pid, sim in scored if sim >= cutoff}


_MARKDOWN_KV_LINE = re.compile(r"(?m)^[ \t]*\*{0,2}[^*\r\n:]{2,40}\*{0,2}:\*{0,2}[ \t]+[^\r\n]+")
# Structural multi-word proper-noun detector (e.g. 'Örnek Technical University', 'Cedar University').
_NAMED_ENTITY_PHRASE = re.compile(r"\b[A-ZÇĞİÖŞÜ][a-zçğıöşüA-ZÇĞİÖŞÜ]+(?:\s+[A-ZÇĞİÖŞÜ][a-zçğıöşüA-ZÇĞİÖŞÜ]+)+\b")


def score_canonical_field_support(
    unit_kind: str,
    text: str,
    requested_fields: list[str],
) -> tuple[str, float, tuple[int, int, str] | None] | None:
    """Semantically match a canonical section to requested enrollment fields without domain regexes.

    For structured markdown key-value records (e.g., transcripts), isolates the single best
    matching `**Key:** Value` line so unrelated sensitive fields (e.g. Student No) are not leaked.
    """
    if not requested_fields or not (text or "").strip():
        return None
    protos = _intent_prototypes()
    if protos is None:
        return None
    if unit_kind == "core" and "institution" in requested_fields:
        # Evaluate sentences to separate current enrollment/placement from former/graduated lines.
        sentences = [s.strip() for s in re.split(r"\n|(?<=[.!?])\s+", text) if len(s.strip()) >= 8]
        best_inst = 0.0
        for sent in sentences:
            sv = _cached_passage_vec(sent)
            if sv is None:
                continue
            s_inst = float(np.dot(sv, protos["sec_institution"]))
            s_past = float(np.dot(sv, protos["sec_past"]))
            if s_inst >= 0.42 and s_inst > s_past + 0.04:
                # Prefer sentences that name a concrete multi-word proper-noun entity over generic statements.
                entity_bonus = 0.20 if _NAMED_ENTITY_PHRASE.search(sent) else 0.0
                best_inst = max(best_inst, s_inst - 0.25 * s_past + entity_bonus)
        if best_inst > 0.0:
            return ("institution", best_inst, None)
    if unit_kind != "person" and "program" in requested_fields:
        best_line: tuple[float, int, int, str] | None = None
        for m in _MARKDOWN_KV_LINE.finditer(text):
            raw = m.group(0)
            val = raw.strip()
            lv = _cached_passage_vec(val)
            if lv is None:
                continue
            s_prog = float(np.dot(lv, protos["sec_program"]))
            s_id = float(np.dot(lv, protos["sec_student_id"]))
            if s_prog >= 0.24 and s_prog > s_id + 0.05:
                if best_line is None or s_prog > best_line[0]:
                    lead_ws = len(raw) - len(raw.lstrip())
                    best_line = (s_prog, m.start() + lead_ws, m.start() + lead_ws + len(val), val)
        if best_line is not None:
            return ("program", best_line[0], (best_line[1], best_line[2], best_line[3]))
    return None


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
