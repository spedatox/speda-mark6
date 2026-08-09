"""
Surprisal — which observations deserve Orion's attention tonight.

Orion's consolidation pass (prompts/agents/orion/02_audit.md, Pass 6) has a
scaling problem the moment the observation store is more than a few hundred rows:
reading everything every night is both expensive and useless, because almost
nothing changed and almost everything is unremarkable. Honcho solves this with
*surprisal* (plastic-labs/honcho, src/dreamer/surprisal.py) — score each
observation by how unexpected it is against the rest of the store, then spend the
reasoning budget on the top slice.

**What we kept and what we changed.** Honcho ships six spatial index
implementations (cover tree, RP tree, LSH, k-means prototypes, a graph, a sklearn
wrapper) because it runs across many workspaces at a scale where an exact
all-pairs comparison is impossible. Mark VI has one owner and observations
numbering in the thousands, where an N×N similarity matrix is a few tens of
megabytes and one numpy call — so the approximate structures would be strictly
worse here: more code, more dependencies (sklearn, for k-means), and a less
accurate answer than the exact computation they approximate. We compute it
exactly.

**And we use both tails, not just one.** Honcho takes the top N% by surprisal.
That finds the novel, but the *dense* end of the same distribution is just as
actionable and Orion had no way to see it: a cluster of near-identical
observations is a merge waiting to happen, and until now his dedup pass could
only catch exact string matches. One computation, two findings:

    high surprisal → isolated. Nothing connects to it. Worth reasoning about.
    high similarity → redundant. Several records of one fact. Worth merging.

Embeddings are L2-normalized (app/services/embeddings.py), so cosine similarity
is a plain dot product and distance is `1 - similarity`. No normalization step,
no distance metric to choose.
"""

import logging
from dataclasses import dataclass

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.observation import Observation

logger = logging.getLogger(__name__)

# Cap on the sample pulled into memory. The similarity matrix is N² float32, so
# 4000 observations is ~64 MB — the point past which this stops being free on a
# Contabo box that is also serving chat. Below it, exact beats approximate.
MAX_SAMPLE = 4_000
DEFAULT_SAMPLE = 2_000

# Neighbours averaged to estimate local density. Too low and a single accidental
# near-duplicate makes a genuinely novel fact look ordinary; too high and
# everything regresses to the store's mean. 5 is Honcho's TREE_K default.
DEFAULT_K = 5

# Cosine similarity above which two observations are treated as the same fact
# said twice. Deliberately high: a false merge destroys a distinction the owner
# may care about, while a missed merge costs one redundant line that the next
# audit can still catch.
DUPLICATE_THRESHOLD = 0.93


@dataclass
class Scored:
    observation: Observation
    score: float


@dataclass
class DuplicatePair:
    a: Observation
    b: Observation
    similarity: float


async def _load_embedded(
    db: AsyncSession, *, user_id: int, sample_size: int
) -> tuple[list[Observation], np.ndarray | None]:
    """Live, embedded observations plus their vectors as one (N, D) matrix.

    Rows whose embedding never landed are excluded rather than zero-filled: a
    zero vector is equidistant from everything and would read as maximally
    surprising, which would hand Orion the store's failures instead of its
    discoveries.
    """
    size = max(1, min(sample_size, MAX_SAMPLE))
    rows = list(
        (
            await db.execute(
                select(Observation)
                .where(
                    Observation.user_id == user_id,
                    Observation.deleted_at.is_(None),
                    Observation.embedding.is_not(None),
                )
                .order_by(Observation.created_at.desc())
                .limit(size)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return [], None

    try:
        matrix = np.stack(
            [np.frombuffer(o.embedding, dtype=np.float32) for o in rows]
        )
    except ValueError as e:
        # Mixed dimensions — the embedding model changed under a partially
        # indexed store. Fail loud rather than silently comparing across models.
        logger.error("surprisal_matrix_failed", extra={"error": str(e)})
        return [], None
    return rows, matrix


def _similarity_matrix(matrix: np.ndarray) -> np.ndarray:
    """All-pairs cosine similarity with self-matches removed.

    Vectors are already L2-normalized, so the Gram matrix IS the cosine
    similarity. The diagonal is forced to -1 so an observation is never its own
    nearest neighbour, which would otherwise make everything look perfectly
    ordinary.
    """
    sims = matrix @ matrix.T
    np.fill_diagonal(sims, -1.0)
    return sims


async def rank_by_surprisal(
    db: AsyncSession,
    *,
    user_id: int,
    limit: int = 15,
    k: int = DEFAULT_K,
    sample_size: int = DEFAULT_SAMPLE,
) -> list[Scored]:
    """
    The store's most isolated observations, most surprising first.

    Surprisal is `1 - mean(similarity to the k nearest other observations)`,
    then min-max normalized to [0, 1] so the score reads the same regardless of
    how tightly the store happens to cluster. A fact whose neighbours are all
    distant is one nothing else in memory connects to — either a genuinely new
    thread worth reasoning about, or something misfiled. Both are Orion's
    business; neither is visible by reading the store in date order.
    """
    rows, matrix = await _load_embedded(db, user_id=user_id, sample_size=sample_size)
    if matrix is None:
        return []

    n = len(rows)
    neighbours = min(k, n - 1)
    if neighbours < 1:
        # One observation has no neighbourhood, so it has no surprisal — not
        # infinite surprisal, which is what a naive implementation would report.
        logger.info("surprisal_skipped", extra={"reason": "too few observations", "count": n})
        return []

    sims = _similarity_matrix(matrix)
    # Partial sort is enough: we need the k largest per row, not a full ordering.
    top_k = np.partition(sims, -neighbours, axis=1)[:, -neighbours:]
    density = top_k.mean(axis=1)
    raw = 1.0 - density

    lo, hi = float(raw.min()), float(raw.max())
    if hi - lo < 1e-9:
        normalized = np.full_like(raw, 0.5)
    else:
        normalized = (raw - lo) / (hi - lo)

    order = np.argsort(-normalized)[:limit]
    scored = [Scored(rows[int(i)], float(normalized[int(i)])) for i in order]

    logger.info(
        "surprisal_ranked",
        extra={
            "sampled": n,
            "k": neighbours,
            "returned": len(scored),
            "top": round(scored[0].score, 3) if scored else None,
        },
    )
    return scored


async def find_near_duplicates(
    db: AsyncSession,
    *,
    user_id: int,
    threshold: float = DUPLICATE_THRESHOLD,
    limit: int = 20,
    sample_size: int = DEFAULT_SAMPLE,
) -> list[DuplicatePair]:
    """
    Pairs of observations that are almost certainly the same fact recorded twice.

    The dense tail of the surprisal computation, and the half Honcho does not
    use. `record_observation` already collapses EXACT repeats, so everything
    surfaced here is a rewording — "wants totals before breakdowns" against
    "prefers the total first, then the detail" — which no string comparison
    catches and which quietly inflates the store's apparent evidence for a claim.

    Pairs are returned, never merged. Deciding that two sentences say one thing
    is a judgement about meaning, and the whole point of the file law is that
    judgement belongs to the custodian's turn, not to a threshold.
    """
    rows, matrix = await _load_embedded(db, user_id=user_id, sample_size=sample_size)
    if matrix is None or len(rows) < 2:
        return []

    sims = _similarity_matrix(matrix)
    # Upper triangle only — (a, b) and (b, a) are one finding, not two.
    upper = np.triu(sims, k=1)
    hits = np.argwhere(upper >= threshold)
    if hits.size == 0:
        logger.info("near_duplicates_none", extra={"sampled": len(rows), "threshold": threshold})
        return []

    scores = upper[hits[:, 0], hits[:, 1]]
    order = np.argsort(-scores)[:limit]

    pairs = [
        DuplicatePair(
            a=rows[int(hits[int(i)][0])],
            b=rows[int(hits[int(i)][1])],
            similarity=float(scores[int(i)]),
        )
        for i in order
    ]
    logger.info(
        "near_duplicates_found",
        extra={"sampled": len(rows), "pairs": len(pairs), "threshold": threshold},
    )
    return pairs


def format_duplicates(pairs: list[DuplicatePair]) -> str:
    """Render duplicate pairs so the custodian can act without a second lookup."""
    if not pairs:
        return (
            "No near-duplicate observations found. Exact repeats are already "
            "collapsed into reinforcement at write time, so a clean result here "
            "means the store genuinely has no redundant rewordings."
        )
    out = [
        f"{len(pairs)} near-duplicate pair(s), closest first. These are separate "
        f"rows saying the same thing in different words — each one inflates the "
        f"apparent evidence for a claim. Merge deliberately: keep the better-worded "
        f"or better-sourced row, demote the other, and only when you are sure they "
        f"mean the same thing.",
        "",
    ]
    for pair in pairs:
        out.append(f"similarity {pair.similarity:.3f}")
        out.append(
            f"  [id:{pair.a.id}] ({pair.a.observer}, "
            f"{pair.a.created_at.strftime('%Y-%m-%d')}, seen {pair.a.reinforcement_count}×) "
            f"{pair.a.content}"
        )
        out.append(
            f"  [id:{pair.b.id}] ({pair.b.observer}, "
            f"{pair.b.created_at.strftime('%Y-%m-%d')}, seen {pair.b.reinforcement_count}×) "
            f"{pair.b.content}"
        )
        out.append("")
    return "\n".join(out)
