# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Cross-lingual recall: reaching an English store with a Turkish question.

The owner's memory store is English by policy — one language means one embedding
neighbourhood per concept and one alphabet in the lexical index, instead of every
fact scattered across two of each. His questions, though, are frequently Turkish,
and that asymmetry was measurable: after the store was repaired and distilled,
English recall reached 87% hit@5 while Turkish sat at 43% on the same probe set
(evals/recall). Same facts, same ranker, half the recall.

Both halves of hybrid retrieval fail on a Turkish query, and they fail for
different reasons — which is why the fix has to sit in front of both rather than
inside either:

  * **The vector half** degrades. text-embedding-3-small is trained
    cross-lingually and does place "ne zaman doğdu" near birth-related English
    text, but not nearly as close as "when was he born" — close enough to rank
    noise above the answer, and after the relevance floor, close enough to be cut
    entirely. Two of the Turkish probes came back empty rather than wrong.
  * **The lexical half** fails outright. BM25 matches tokens. "doğum" and "birth"
    share none. FTS5 returned zero hits for most Turkish queries, so the fusion
    that is supposed to rescue the vector half had nothing to contribute.

So a Turkish query is TRANSLATED before retrieval, and the translation is used
ALONGSIDE the original rather than instead of it:

  * the vector pass embeds the English translation, which is what puts the query
    in the same neighbourhood as the facts;
  * the lexical pass searches both, because the original is what still matches
    the Turkish proper nouns the English store legitimately keeps — "Uludağ",
    "OSTİM", "Akçalar", "TDV Altındağ" are spelled the same in both languages
    and are exactly the rare tokens BM25 is there to catch.

Translation is cached per-process by folded query text. Recall queries repeat
heavily — the same handful of questions asked in the same words — so the cache
absorbs nearly all of the cost after the first ask, and a miss costs one small
completion on the background model.

Nothing here raises. A failed or disabled translation returns the query
unchanged, which is exactly the behaviour that existed before this module, and
degrading to "Turkish recall is worse" is always better than degrading to "recall
is down".
"""

import logging
import re

logger = logging.getLogger(__name__)

# Bounded so a long-running process cannot accumulate translations forever. The
# working set is tiny — a person asks a few dozen distinct questions — so this is
# a safety limit, not a tuning parameter.
_CACHE_MAX = 512
_cache: dict[str, str] = {}

# Turkish detection by function words, not by alphabet. The alphabet test is
# useless here: an English question about his memory routinely contains Uludağ,
# OSTİM or Akçalar, and flagging those as Turkish would send every English query
# through a needless translation. Grammar words are what actually separate the
# two languages.
_TR_WORDS = re.compile(
    r"\b(ne|nasıl|neden|nerede|nereye|kaç|hangi|niye|kim|kimin|zaman|kadar|"
    r"mi|mı|mu|mü|ve|bir|için|ile|olarak|değil|var|yok|bu|şu|çok|daha|gibi|"
    r"benim|onun|senin)\b",
    re.IGNORECASE,
)
_EN_WORDS = re.compile(
    r"\b(what|when|where|which|who|whose|why|how|does|did|is|are|was|were|has|"
    r"have|the|his|her|their|of|to|in|on|at|for|about|and|with|much|many)\b",
    re.IGNORECASE,
)

_PROMPT = """\
Translate this search query from Turkish to English. It will be used to search a
personal memory store written in English.

Rules:
- Return ONLY the translated query. No quotes, no explanation, no preamble.
- Keep proper nouns exactly as written: names of people, places, universities,
  companies, exams and currencies (Uludağ, OSTİM, Akçalar, YDS, DGS, TL).
- Translate the QUESTION into the words the ANSWER would be written in. A stored
  fact says "was born on"; so "ne zaman doğdu" becomes "when was he born", not
  "what time did birth happen".
- If the query is already English, return it unchanged.

Query: {query}"""


def looks_turkish(query: str) -> bool:
    """Whether a query is Turkish enough to be worth translating.

    Counts Turkish grammar words against English ones. Ties go to English —
    translating an English query is wasted latency, while leaving a genuinely
    Turkish one alone only costs what recall already cost before this existed.
    """
    body = query or ""
    return len(_TR_WORDS.findall(body)) > len(_EN_WORDS.findall(body))


def _cache_key(query: str) -> str:
    return " ".join((query or "").lower().split())


async def to_english(query: str) -> str:
    """The English form of `query`, or `query` itself if it is already English,
    translation is disabled, or the call fails.

    Never raises: every caller is on a retrieval hot path where an exception
    would turn a degraded search into no search at all.
    """
    from app.config import settings
    from app.services.llm_client import LLMClient

    text = (query or "").strip()
    if not text or not settings.recall_translate_queries or not looks_turkish(text):
        return text

    key = _cache_key(text)
    hit = _cache.get(key)
    if hit is not None:
        return hit

    model = (
        settings.recall_translation_model
        or settings.llm_background_model
        or settings.llm_main_model
    )
    if not model:
        return text

    try:
        resp = await LLMClient().create_message(
            model=model,
            system="You translate search queries. You return only the translation.",
            messages=[{"role": "user", "content": _PROMPT.format(query=text)}],
            max_tokens=200,
            # Same trap app/services/memory.py documents for title generation:
            # a reasoning model spends the whole budget thinking and returns an
            # empty message. This is translation, not deliberation.
            reasoning_effort="minimal",
        )
        translated = (resp.content[0].text.strip() if resp.content else "")
        # First non-empty line only — some providers add a trailing note however
        # firmly the prompt says not to.
        translated = next(
            (ln.strip().strip('"') for ln in translated.splitlines() if ln.strip()), ""
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("recall_query_translation_failed", extra={"error": str(e)})
        return text

    if not translated:
        return text

    if len(_cache) >= _CACHE_MAX:
        _cache.clear()
    _cache[key] = translated
    logger.info(
        "recall_query_translated",
        extra={"query": text[:120], "translated": translated[:120]},
    )
    return translated


async def expand(query: str) -> tuple[str, str]:
    """Return (vector_query, lexical_query) for one user query.

    The vector query is the English form — the point of translating is to put the
    query in the same region of the space as the facts. The lexical query is BOTH
    joined, because the two halves want opposite things from a Turkish question:
    meaning wants it in the store's language, while BM25 wants every literal
    token the user actually typed, including the Turkish proper nouns that are
    spelled identically in the English store and are the rare, identifying terms
    the keyword pass exists to catch.
    """
    english = await to_english(query)
    if english == query:
        return query, query
    return english, f"{query} {english}"
