"""
Deduplication + Turkish-aware text normalization for the news collector.

Two-stage dedup keeps one row per story even when NTV, Hürriyet and Sabah all
run it at once — which is exactly what protects the Tier-2 API budget:

1. Canonical URL — lowercased host+path with tracking query params stripped.
2. Normalized-title hash — lowercased, Turkish diacritics folded, punctuation
   and short stopwords removed, first N content tokens hashed. Near-identical
   headlines across outlets collapse to the same key.

Everything here is pure and side-effect free so it is trivially unit-testable.
"""

import hashlib
import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit

# Query params that never identify the article — drop them for canonical URLs.
_TRACKING_PARAMS = re.compile(
    r"^(utm_|fbclid$|gclid$|ref$|ref_src$|_ga$|mc_cid$|mc_eid$|spm$)", re.I
)

# Turkish char folding so "Şırnak"/"sirnak" and "OSTİM"/"ostim" match. Applied
# after lowercasing (Turkish 'İ'.lower() → 'i̇' with a combining dot, handled).
_TR_FOLD = str.maketrans({
    "ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
    "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c",
    "â": "a", "î": "i", "û": "u",
})

# Short filler words that add no dedup signal (TR + a few EN).
_STOPWORDS = frozenset({
    "ve", "ile", "bir", "bu", "da", "de", "mi", "için", "the", "a", "an",
    "to", "of", "in", "on", "son", "dakika",
})

_TITLE_TOKENS = 8  # how many leading content tokens define a story's identity


def normalize_text(s: str) -> str:
    """Lowercase, fold Turkish diacritics, collapse whitespace. Used for both
    title hashing and keyword matching so the two agree on what 'the same' is.

    The fold runs BEFORE casefold (so 'İ'→'i' happens on the composed char), and
    any combining marks that survive — e.g. Turkish 'İ'.casefold() decomposes to
    'i' + U+0307 — are stripped, so "OSTİM" and "ostim" match."""
    s = (s or "").translate(_TR_FOLD).casefold()
    s = "".join(ch for ch in unicodedata.normalize("NFD", s)
                if not unicodedata.combining(ch))
    s = re.sub(r"\s+", " ", s).strip()
    return s


def canonical_url(url: str) -> str:
    """Canonicalize a URL for exact-match dedup: lowercase scheme+host, keep the
    path, drop tracking query params, drop the fragment and any trailing slash."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    host = parts.netloc.lower()
    path = parts.path.rstrip("/") or "/"
    kept = [
        kv for kv in parts.query.split("&")
        if kv and not _TRACKING_PARAMS.match(kv.split("=", 1)[0])
    ]
    query = "&".join(sorted(kept))
    return urlunsplit((parts.scheme.lower(), host, path, query, ""))


def title_hash(title: str) -> str:
    """A stable fingerprint of a headline's meaning: normalize, strip
    punctuation, drop stopwords, hash the first few content tokens. Two outlets'
    wording of the same story yields the same hash."""
    norm = normalize_text(title)
    norm = re.sub(r"[^\w\s]", " ", norm)
    tokens = [t for t in norm.split() if t and t not in _STOPWORDS]
    key = " ".join(tokens[:_TITLE_TOKENS])
    return hashlib.sha1(key.encode("utf-8")).hexdigest()  # noqa: S324 (not security)
