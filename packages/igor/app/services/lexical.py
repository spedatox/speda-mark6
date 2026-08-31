# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The keyword half of recall, and the fusion that joins it to the vector half.

Recall was vector-only, and vector-only recall fails in one specific, very
visible way: it is excellent at paraphrase and bad at rare literal tokens. Ask it
about a concept and it finds the conversation; ask it about `BLM2107`, `Efdal`,
`ostimteknik`, an IBAN or a drug name, and a near-synonym of the surrounding
prose outranks the row that literally contains the string. Dense embeddings
compress exactly the information that makes a rare token identifying.

The fix is not a bigger vector store — the store is not the problem, and at one
owner's scale a brute-force dot product over a few thousand normalized vectors is
already fast. The fix is to run a lexical retriever alongside it and fuse the two
rankings. Published results put hybrid RRF meaningfully above either half alone
(≈91% recall@10 versus 65–78% for BM25 or KNN separately on the same corpus), and
the reason is structural rather than incidental: the two retrievers fail on
different queries, so the union covers both.

**Reciprocal Rank Fusion** is what does the joining, and it is deliberately
rank-only. A BM25 score and a cosine similarity are not comparable numbers; any
weighted sum of them is a hidden guess about scale that breaks the moment the
corpus grows. RRF throws the scores away and keeps the order: each list
contributes `1/(k + rank)`, and the sums decide. k=60 is the constant from the
original paper and the default nearly every implementation ships.

**Turkish is the reason for two specific choices.** The owner's material is
Turkish and English mixed in one store:

  * Text is **folded to ASCII by us**, at index time and query time both, before
    FTS5 ever sees it. The tokeniser's own `remove_diacritics 2` handles ş→s,
    ğ→g, ö→o, ü→u, ç→c — but it cannot touch Turkish's dotless **ı**, because ı
    is not "i with something removed", it is a separate letter with no
    decomposition. Left to unicode61, `sinav` typed on an English keyboard
    misses every stored `sınav`, which is the single most common Turkish query
    in this system. `fold()` below closes that, and folding both sides keeps the
    index and the query in the same alphabet by construction.
  * Every term is searched as a PREFIX (`sinav*`). Turkish is agglutinative:
    "sınav", "sınavı", "sınavda", "sınavlarım" are the same word wearing
    different suffixes, and no stemmer ships with FTS5 for it. Prefix matching
    recovers most of what a stemmer would, and costs one character.

FTS5 is SQLite-only. On Postgres — which this project can be pointed at — every
function here degrades to "no lexical results", and fusion with an empty list is
exactly the old vector-only behaviour. Nothing raises.
"""

import logging
import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# RRF's smoothing constant. Large enough that the top few ranks of one list
# cannot single-handedly overrule broad agreement across both.
RRF_K = 60


class Index:
    """One FTS5 table: its name, its columns, and the BM25 weight of each.

    Two corpora need the keyword half — the distilled facts (`search_memory`)
    and the raw transcript (`recall_conversations`) — and they differ only in
    what a "row" is. Naming that difference in data keeps one implementation of
    the tokeniser, the query builder, the injection guard and the backfill,
    instead of two that drift.
    """

    def __init__(self, table: str, columns: list[str], weights: list[float]) -> None:
        self.table = table
        self.columns = columns
        self.weights = weights

    @property
    def bm25(self) -> str:
        return f"bm25({self.table}, " + ", ".join(str(w) for w in self.weights) + ")"


# A fact's subject outweighs its prose: a row ABOUT a course code should beat one
# that mentions it in passing.
OBSERVATIONS = Index(
    "observations_fts", ["content", "subject", "domain", "observer"],
    [1.0, 2.0, 0.5, 0.5],
)
# A message has one field, so there is nothing to weight against.
MESSAGES = Index("messages_fts", ["text"], [1.0])

# Word characters across scripts — Turkish included — plus digits. Everything
# else is dropped, which doubles as the injection guard: the punctuation FTS5
# syntax is made of (quotes, parens, colons, ^, *, -) cannot survive this
# filter, and fold()'s lowercasing disarms the word-shaped operators, since
# AND / OR / NOT / NEAR are operators only in upper case.
_TOKEN = re.compile(r"[^\W_]{2,}", re.UNICODE)

# Function words carry no retrieval signal but do match nearly everything once
# prefixed. Dropping them is what stops `ve*` and `the*` from flattening the
# ranking. The Turkish QUESTION words matter most: a recall query is usually
# phrased as a question ("vize ne zaman", "kira ne kadar"), so they appear in
# almost every query while identifying almost nothing. Written folded (ASCII),
# because fold() runs before this set is consulted.
_STOP = frozenset({
    # English
    "the", "and", "for", "with", "that", "this", "was", "are", "has", "have",
    "not", "but", "you", "his", "her", "its", "from", "what", "when", "which",
    "how", "why", "who", "where", "does", "did", "about",
    # Turkish function words
    "ile", "bir", "bu", "su", "ama", "icin", "gibi", "daha", "olan",
    "olarak", "var", "yok", "ise", "veya", "ancak", "ki", "de", "da", "mi",
    "mu", "ya", "cok", "en", "her", "ve",
    # Turkish question words — "ne zaman", "ne kadar", "kim", "hangi"…
    "ne", "kim", "nasil", "neden", "nerede", "nereye", "kac", "hangi", "niye",
    "zaman", "kadar", "kimin",
})

# Minimum length before a term is searched as a PREFIX. Prefixing exists for
# Turkish agglutination — "sinav" reaching "sinavlarinda" — and real stems are
# four characters or more. Below that a prefix stops being a stem and becomes a
# wildcard: `ne*` matches "net", "neden", "nerede" and drowns the actual hit,
# which is exactly how "vize ne zaman" returned a fact about morning replies.
# Short tokens ("tl", "ai", "vpn") are still searched — just matched exactly.
_PREFIX_MIN = 4


# Turkish letters that unicode61 cannot fold on its own. ı/İ are the load-bearing
# pair — the others are here so one pass produces a single consistent alphabet
# rather than leaving the tokeniser to finish the job differently.
_FOLD = str.maketrans({
    "ı": "i", "İ": "i", "I": "i",
    "ş": "s", "Ş": "s",
    "ğ": "g", "Ğ": "g",
    "ç": "c", "Ç": "c",
    "ö": "o", "Ö": "o",
    "ü": "u", "Ü": "u",
})


def fold(value: str) -> str:
    """Normalise text into the alphabet the index is built in.

    Applied to BOTH sides — every value written to an FTS row and every query
    term — so "sinav", "sınav" and "SINAV" are one token. Note this is a SEARCH
    normalisation only: the folded string is never displayed and never replaces
    the real text, which lives in the observations/messages tables.

    `.translate` before `.lower()` on purpose: Python lowercases Turkish 'İ' to
    'i' plus a combining dot, which would put a two-codepoint token in the index
    that no reasonable query ever reproduces.
    """
    return (value or "").translate(_FOLD).lower()


def _is_sqlite(db: AsyncSession) -> bool:
    """Whether FTS5 is even a possibility on this deployment.

    Read off the configured URL rather than introspected from the session — the
    same test app/database.py makes to decide its pragmas, so the two can never
    disagree about which engine is underneath.
    """
    from app.config import settings

    return settings.database_url.startswith("sqlite")


def build_match_query(query: str) -> str:
    """Turn free text into an FTS5 MATCH expression, or "" if nothing survives.

    Terms are ORed rather than ANDed: a recall query is a description, not a
    filter, and requiring every word means one incidental term ("hakkında",
    "about") returns nothing at all. BM25 already rewards rows that match more
    of them, so OR-plus-ranking gets the behaviour ANDing was reaching for
    without the cliff.
    """
    terms = [t for t in _TOKEN.findall(fold(query)) if t not in _STOP][:24]
    if not terms:
        return ""
    return " OR ".join(
        f"{t}*" if len(t) >= _PREFIX_MIN else t for t in terms
    )


async def ensure_index(db: AsyncSession, index: Index = OBSERVATIONS) -> bool:
    """Create the FTS5 table if it is missing. Returns whether it is usable.

    A standalone (not external-content) table keyed by the source row's id in
    `rowid`: the alternative, `content=observations`, would need triggers on a
    table SQLAlchemy owns and would break the moment a migration rebuilt it.
    Duplicating a few thousand short strings is the cheaper trade.
    """
    if not _is_sqlite(db):
        return False
    try:
        await db.execute(text(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {index.table} USING fts5("
            f"  {', '.join(index.columns)},"
            "  tokenize='unicode61 remove_diacritics 2'"
            ")"
        ))
        return True
    except Exception as e:  # noqa: BLE001
        # A SQLite built without FTS5 is a legitimate deployment, not a bug —
        # recall degrades to vector-only and says so once, in the log.
        logger.warning("fts_unavailable", extra={"table": index.table, "error": str(e)})
        return False


async def index_row(
    db: AsyncSession, index: Index, row_id: int, values: dict[str, str]
) -> None:
    """Add or replace one row in a lexical index. Never raises — a fact that
    failed to index is still recorded, still embedded, and still findable by
    meaning; losing the write path over a search index would be the worse bug."""
    if not await ensure_index(db, index):
        return
    try:
        await db.execute(
            text(f"DELETE FROM {index.table} WHERE rowid = :id"), {"id": row_id}
        )
        cols = ", ".join(index.columns)
        binds = ", ".join(f":{c}" for c in index.columns)
        await db.execute(
            text(f"INSERT INTO {index.table}(rowid, {cols}) VALUES (:id, {binds})"),
            {"id": row_id, **{c: fold(values.get(c) or "") for c in index.columns}},
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "fts_index_failed",
            extra={"table": index.table, "row_id": row_id, "error": str(e)},
        )


async def index_observation(db: AsyncSession, obs) -> None:
    """Index one Observation. The one caller that knows the fact schema."""
    await index_row(db, OBSERVATIONS, obs.id, {
        "content": obs.content,
        "subject": obs.subject,
        "domain": obs.domain,
        "observer": obs.observer,
    })


async def index_message(db: AsyncSession, message_id: int, body: str) -> None:
    """Index one message's plain text, keyed by its message id — the same key
    MessageEmbedding uses, so the two halves of recall address the same rows."""
    await index_row(db, MESSAGES, message_id, {"text": body})


async def drop_row(db: AsyncSession, index: Index, row_id: int) -> None:
    """Remove one row from a lexical index (hard deletes only — a soft-deleted
    observation is filtered by the caller's own id set, not here)."""
    if not _is_sqlite(db):
        return
    try:
        await db.execute(
            text(f"DELETE FROM {index.table} WHERE rowid = :id"), {"id": row_id}
        )
    except Exception:  # noqa: BLE001
        pass


async def indexed_ids(db: AsyncSession, index: Index = OBSERVATIONS) -> set[int]:
    """Every rowid the index already holds. Backfills invert their gap query
    through this, because the gap lives in a table the ORM does not model."""
    if not await ensure_index(db, index):
        return set()
    try:
        rows = await db.execute(text(f"SELECT rowid FROM {index.table}"))
        return {int(r[0]) for r in rows.all()}
    except Exception:  # noqa: BLE001
        return set()


async def search(
    db: AsyncSession,
    *,
    query: str,
    limit: int = 50,
    allowed_ids: set[int] | None = None,
    index: Index = OBSERVATIONS,
) -> list[int]:
    """Row ids ranked by BM25, best first.

    `allowed_ids` is the candidate set the caller already narrowed by owner,
    validity, date and every other filter. Filtering here rather than joining
    keeps this function ignorant of both schemas — it only ever knows rowids —
    and the candidate set is already bounded by the caller's own scan cap.
    """
    match = build_match_query(query)
    if not match or not await ensure_index(db, index):
        return []
    try:
        # bm25() returns a NEGATIVE number where more negative is better, so
        # plain ascending ORDER BY is already best-first.
        rows = await db.execute(
            text(
                f"SELECT rowid, {index.bm25} AS rank "
                f"FROM {index.table} WHERE {index.table} MATCH :q "
                "ORDER BY rank LIMIT :n"
            ),
            # Over-fetch, because the allowed_ids filter runs after the ranking.
            {"q": match, "n": limit * 4 if allowed_ids else limit},
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("fts_search_failed", extra={"table": index.table, "error": str(e)})
        return []

    ids = [int(r[0]) for r in rows.all()]
    if allowed_ids is not None:
        ids = [i for i in ids if i in allowed_ids]
    return ids[:limit]


async def rebuild(db: AsyncSession, rows) -> int:
    """Re-index a batch of observations from scratch. Returns rows written.

    Used by the backfill and by anything that repairs the record — an index that
    can only be built incrementally is an index that stays wrong after the first
    failed write.
    """
    if not await ensure_index(db, OBSERVATIONS):
        return 0
    written = 0
    for obs in rows:
        await index_observation(db, obs)
        written += 1
    return written


def rrf_fuse(*rankings: list[int], k: int = RRF_K, limit: int = 15) -> list[tuple[int, float]]:
    """Fuse ranked id lists into one, by Reciprocal Rank Fusion.

    Returns (id, score) best-first. The score is normalised so that an id
    ranked first in EVERY list scores 1.0 — the raw RRF sum is a small,
    scale-free number (~0.03) that means nothing to a reader, and these scores
    get printed next to facts a model has to weigh.
    """
    lists = [r for r in rankings if r]
    if not lists:
        return []
    totals: dict[int, float] = {}
    for ranking in lists:
        for rank, item_id in enumerate(ranking, start=1):
            totals[item_id] = totals.get(item_id, 0.0) + 1.0 / (k + rank)

    ceiling = len(lists) * (1.0 / (k + 1))
    fused = [(i, s / ceiling) for i, s in totals.items()]
    fused.sort(key=lambda pair: (-pair[1], pair[0]))
    return fused[:limit]
