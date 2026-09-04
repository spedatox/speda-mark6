# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Semantic recall must not re-read the whole embedding corpus on every call.

The regression this closes, measured on the live deployment before the fix:

    rows 20,635 · matrix 126.8 MB · DB load 6.05 s · stack 0.33 s · dot 0.01 s

Six of those seconds were the BLOB read, for bytes that had not changed since
the previous recall. It ran inside a request coroutine, and uvicorn drops any
WebSocket that misses its 20 s ping deadline — which is how a handful of
overlapping recalls took out every connected client at once, both Forge peers
included. app/services/observations.py had already fixed exactly this for the
fact tier at 12.3 MB / 227 ms; this is the same cache for the message tier.

These pin the cache's contract: it is keyed on a watermark that moves whenever
the corpus does, it hands back the same matrix while nothing has changed, and
the vectors it returns are the ones that were stored.
"""

import numpy as np
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.models.message_embedding import MessageEmbedding
from app.skills.semantic_search import _VECTOR_CACHE, _vectors_for

DIM = 8


def _vec(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(DIM).astype(np.float32)
    return (v / np.linalg.norm(v)).astype(np.float32)


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    _VECTOR_CACHE.clear()
    async with maker() as session:
        yield session
    await engine.dispose()


async def _add(db, *, user_id: int, vec: np.ndarray, mid: int) -> int:
    row = MessageEmbedding(
        message_id=mid, session_id=1, user_id=user_id, agent_id="ultron",
        role="user", text=f"m{mid}", embedding=vec.tobytes(),
    )
    db.add(row)
    await db.commit()
    return row.id


@pytest.mark.asyncio
async def test_the_vectors_returned_are_the_ones_stored(db):
    a, b = _vec(1), _vec(2)
    id_a = await _add(db, user_id=1, vec=a, mid=10)
    id_b = await _add(db, user_id=1, vec=b, mid=11)

    index, matrix = await _vectors_for(db, 1)

    assert set(index) == {id_a, id_b}
    assert np.allclose(matrix[index[id_a]], a)
    assert np.allclose(matrix[index[id_b]], b)


@pytest.mark.asyncio
async def test_an_unchanged_corpus_is_not_read_again(db):
    await _add(db, user_id=1, vec=_vec(1), mid=10)

    _, first = await _vectors_for(db, 1)
    _, second = await _vectors_for(db, 1)

    assert second is first, (
        "a second recall over an unchanged corpus must reuse the cached matrix; "
        "rebuilding it is the 6-second BLOB read this cache exists to remove"
    )


@pytest.mark.asyncio
async def test_a_new_embedding_invalidates_the_cache(db):
    await _add(db, user_id=1, vec=_vec(1), mid=10)
    _, before = await _vectors_for(db, 1)

    new_id = await _add(db, user_id=1, vec=_vec(2), mid=11)
    index, after = await _vectors_for(db, 1)

    assert after is not before, "the watermark must move when a row is added"
    assert new_id in index, "the newly embedded message must be searchable"
    assert after.shape[0] == 2


@pytest.mark.asyncio
async def test_one_owners_vectors_never_leak_into_anothers(db):
    mine = await _add(db, user_id=1, vec=_vec(1), mid=10)
    theirs = await _add(db, user_id=2, vec=_vec(2), mid=11)

    index, matrix = await _vectors_for(db, 1)

    assert mine in index
    assert theirs not in index
    assert matrix.shape[0] == 1


@pytest.mark.asyncio
async def test_an_owner_with_no_embeddings_is_handled(db):
    """Scoring must degrade to the lexical half, not raise on an empty matrix."""
    index, matrix = await _vectors_for(db, 99)

    assert index == {}
    assert matrix.size == 0
    # The scoring line in execute() guards on matrix.size for exactly this.
    assert (matrix @ _vec(1) if matrix.size else np.zeros(0)).shape == (0,)
