"""Hybrid semantic vector, character n-gram, temporal and cross-agent episodic cortex.

Provides zero-provider-call first-turn episodic continuity and semantic archive
retrieval so relative, implicit, typo-heavy or zero-keyword turns ("phewwww.",
"Im on my way still", "how far am I from the destination", "ok bus time changed",
"remebet our conversation last night", "well I talked about it with friday")
receive the right prior conversation episodes before the first model call.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, time, timedelta, timezone
import math
import re
from zoneinfo import ZoneInfo

from app.services.lexical import fold

# Bilingual latent concept dimensions for zero-provider-call semantic vector projection.
# These map relative phrases ("destination", "on my way", "phew", "last night", "score")
# into shared dense semantic dimensions without hardcoded reply rules.
_CONCEPT_DIMENSIONS: dict[str, tuple[str, ...]] = {
    "travel_mobility": (
        "bus", "ticket", "bilet", "otogar", "otogari", "asti", "flixbus", "ulusoy", "obilet",
        "kamil", "koc", "destination", "way", "route", "trip", "journey", "travel", "travelling",
        "traveling", "arrive", "arrival", "depart", "departure", "flight", "train", "yht",
        "commute", "traffic", "distance", "far", "heading", "headed", "ride", "passenger",
        "yol", "yoldayim", "sefer", "kalkis", "varis", "otobus", "tren", "mesafe", "rota",
    ),
    "home_family_location": (
        "bursa", "ankara", "home", "family", "house", "dorm", "dormitory", "caliskanlar",
        "karahidirkoy", "yenisehir", "akcalar", "nilufer", "ostim", "hacettepe", "yapracik",
        "address", "location", "coordinates", "stay", "staying", "visit", "visiting", "weekend",
        "ev", "aile", "yurt", "adres", "konum", "memleket",
    ),
    "academic_exam_grade": (
        "exam", "exams", "midterm", "midterms", "final", "finals", "quiz", "vize", "vizeler",
        "sinav", "score", "grade", "result", "results", "point", "points", "100", "99",
        "study", "studying", "chapter", "course", "courses", "class", "classes", "lecture",
        "professor", "teacher", "hoca", "melike", "bozkurt", "calculator", "casio", "fx", "991cw",
        "statistics", "istatistik", "accounting", "muhasebe", "marketing", "data", "structures",
        "supply", "chain", "logistics", "mis", "mis215", "mis231", "mis243", "mis251", "mar411",
        "wap225", "luck", "phew", "pheww", "phewww", "phewwww", "judgment", "formula", "formulas",
        "closing", "debit", "credit", "journal", "ledger", "not", "puan", "sonuc", "ders",
    ),
    "schedule_time_commitment": (
        "calendar", "schedule", "plan", "planning", "tomorrow", "today", "tonight", "morning",
        "afternoon", "evening", "night", "monday", "tuesday", "wednesday", "thursday", "friday",
        "saturday", "sunday", "satursay", "wedensday", "clear", "clen", "delete", "remove",
        "add", "move", "shift", "changed", "change", "gap", "conflict", "overlap", "meeting",
        "appointment", "reminder", "task", "todo", "deadline", "hour", "hours", "minute", "minutes",
        "takvim", "program", "yarin", "bugun", "iptal", "ekle", "sil", "hatirlatma", "saat",
    ),
    "personal_emotional_social": (
        "lonely", "loneliness", "exhausted", "tired", "sleep", "sleeping", "bed", "feeling",
        "feel", "hurt", "burn", "burned", "impaled", "lost", "dating", "girlfriend", "couple",
        "romantic", "love", "care", "cared", "sevval", "rumeysa", "ilayda", "alper", "kaan",
        "sister", "brother", "sibling", "mother", "father", "friend", "vent", "venting", "rough",
        "yalniz", "yalnizlik", "yorgun", "uyku", "sevgili", "kardes", "abla", "abi",
    ),
    "health_wellbeing": (
        "gym", "workout", "training", "steps", "walk", "walking", "health", "samsung",
        "medication", "medicine", "pill", "lustral", "vitamin", "vitamins", "eat", "eaten",
        "breakfast", "dinner", "sleepy", "ilac", "saglik", "spor", "adim",
    ),
    "system_memory_infra": (
        "memory", "retrieval", "retrival", "retrivial", "recall", "archive", "timeout",
        "timing", "deadline", "budget", "sandbox", "reconciling", "incident", "report",
        "diagnose", "diagnosis", "fix", "patch", "push", "deploy", "container", "postgres",
        "sqlite", "index", "database", "vector", "tool", "tools", "context", "lag", "broken",
    ),
    "security_network": (
        "contabo", "vps", "ddos", "flood", "udp", "47584", "ufw", "firewall", "watchdog",
        "brute", "ssh", "attack", "compromise", "suspended", "suspension", "security", "breach",
    ),
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
    """Project text into latent bilingual semantic concept dimensions."""
    folded = fold(text)
    raw_words = re.findall(r"\w+", folded)
    words = [w for w in raw_words] + [_normalize_token(w) for w in raw_words]
    word_set = set(words)
    vec: Counter[str] = Counter()
    if _RELIEF_OR_OPENING_CUE.match(folded.strip()):
        # Emotional relief / post-event exhale strongly correlates with active exams/commitments.
        vec["academic_exam_grade"] += 3
        vec["schedule_time_commitment"] += 1
    for dim, vocab in _CONCEPT_DIMENSIONS.items():
        hits = 0
        for term in vocab:
            if term in word_set:
                hits += 2
            elif len(term) >= 4 and any(w.startswith(term) or term.startswith(w) for w in word_set if len(w) >= 4):
                hits += 1
        if hits:
            vec[dim] += hits
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
    """Compute hybrid lexical (BM25-like) + char-ngram vector + concept vector score."""
    folded_ep = fold(episode_text)
    if not folded_ep.strip():
        return 0.0
    ep_words = set(re.findall(r"\w+", folded_ep))
    ep_norm_words = ep_words | {_normalize_token(w) for w in ep_words}

    # 1. Lexical / BM25-style weighted term match
    lexical_score = 0.0
    for term in query_terms_list:
        norm_t = _normalize_token(term)
        weight = (idf or {}).get(term, min(len(term), 12) * 1.5)
        if term in ep_words or norm_t in ep_norm_words:
            lexical_score += weight * 3.0
        elif term in folded_ep or (len(norm_t) >= 4 and norm_t in folded_ep):
            lexical_score += weight * 1.8

    # 2. Character n-gram cosine similarity (catches typos like "remebet" / "satursay" / "phewwww")
    q_ng = query_ngrams if query_ngrams is not None else char_ngrams(query)
    ep_ng = char_ngrams(folded_ep[:2400])
    ngram_sim = cosine_counter(q_ng, ep_ng)

    # 3. Latent semantic concept cosine similarity (catches relative phrases like
    # "how far am I from the destination" -> Bursa trip, "phewwww" -> exam)
    q_cp = query_concepts if query_concepts is not None else concept_vector(query)
    ep_cp = concept_vector(folded_ep[:2400])
    concept_sim = cosine_counter(q_cp, ep_cp)
    shared_concepts = len(set(q_cp.keys()) & set(ep_cp.keys()))

    # 4. Temporal decay bonus (recent 72h episodes get a smooth recency boost)
    recency_bonus = max(0.0, 12.0 * math.exp(-max(0.0, age_hours) / 36.0)) if (
        lexical_score > 0 or ngram_sim >= 0.14 or concept_sim >= 0.35
    ) else 0.0

    total = (
        lexical_score
        + (28.0 * ngram_sim if ngram_sim >= 0.10 else 0.0)
        + (24.0 * concept_sim + 4.0 * shared_concepts if concept_sim >= 0.30 else 0.0)
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
    # Match explicit conversational references anywhere in the owner's message,
    # while avoiding bare weekday false positives ("this friday was rough").
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
    if user_turn_count <= 3 and any(
        phrase in folded
        for phrase in (
            "on my way", "from the destination", "the destination", "how far am i",
            "time changed", "bus time", "last night", "our conversation",
            "wish me luck", "result released", "know my", "remember", "remebet",
        )
    ):
        return True
    return False
