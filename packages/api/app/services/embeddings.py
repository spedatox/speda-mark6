"""
Embedding generation for semantic recall (app/skills/semantic_search.py).

Always OpenAI, regardless of which provider the active chat model uses —
text-embedding-3-small is cheap and reliable, and this reuses the AsyncOpenAI
client already a dependency of this project (see llm_client.py's _compat_client
for the analogous pattern on the chat-completions side).

Vectors are returned L2-normalized so that cosine similarity at query time
reduces to a plain dot product (see embedding_indexer.py / semantic_search.py).
"""

import logging

import numpy as np
from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


async def embed_texts(texts: list[str]) -> list[np.ndarray]:
    """
    Embed a batch of texts in a single API call. Returns one L2-normalized
    float32 vector per input text, same order as `texts`. Raises on failure —
    callers (background tasks) are expected to catch and skip, same tolerance
    pattern used throughout memory.py/history_indexer.py.
    """
    if not texts:
        return []
    resp = await _get_client().embeddings.create(model=settings.embedding_model, input=texts)
    vectors = []
    for item in resp.data:
        vec = np.array(item.embedding, dtype=np.float32)
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        vectors.append(vec)
    return vectors
