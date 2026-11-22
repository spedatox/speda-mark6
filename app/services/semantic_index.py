"""Local multilingual neural embeddings, persistent vector store, RRF fusion, and MMR diversity.

Runs in-process on CPU via quantized ONNX (`intfloat/multilingual-e5-small`, 384d)
with zero foreground provider calls and zero external vector database dependency.
Stores normalized float16 vectors in the additive `memory_embeddings` table (both
SQLite and PostgreSQL) and searches contiguous float32 NumPy matrices in <2ms.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import logging
import os
import threading
from typing import Iterable

import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.models.memory_serving import MemoryChange, MemoryEmbedding, MemoryServingUnit

logger = logging.getLogger(__name__)

DEFAULT_MODEL_ID = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DEFAULT_DIMS = 384


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def pack_vector(vec: np.ndarray) -> bytes:
    arr = np.asarray(vec, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(arr))
    if norm > 0.0:
        arr = arr / norm
    return arr.astype(np.float16).tobytes()


def unpack_vector(raw: bytes, dims: int = DEFAULT_DIMS) -> np.ndarray:
    arr = np.frombuffer(raw, dtype=np.float16).astype(np.float32)
    if arr.size != dims:
        raise ValueError(f"Expected {dims} dims, got {arr.size}")
    return arr


def _unit_passage_text(title: str, aliases: Iterable[str], section_text: str) -> str:
    alias_str = ", ".join(str(a).strip() for a in (aliases or ()) if str(a).strip())
    header = f"{title} ({alias_str})" if alias_str else str(title or "")
    body = str(section_text or "").strip()
    if header:
        return f"{header}: {body}"[:1500]
    return body[:1500]


def _source_passage_text(source: MemoryChange) -> str:
    prior = ((source.provenance or {}).get("prior_context") or {}).get("text", "")
    body = str(source.content or "").strip()
    if prior:
        return f"{prior[:300]}\n{body}"[:1500]
    return body[:1500]


class NeuralEmbedder:
    """Process-wide lazy-loaded multilingual ONNX embedder (`multilingual-e5-small`)."""

    _lock = threading.Lock()
    _instances: dict[str, "NeuralEmbedder"] = {}

    def __init__(self, model_name: str = DEFAULT_MODEL_ID, *, cache_dir: str | None = None):
        self.model_name = model_name
        self.cache_dir = cache_dir or os.getenv("FASTEMBED_CACHE_PATH") or None
        self.dims = DEFAULT_DIMS
        self._model = None
        self._model_lock = threading.Lock()

    @classmethod
    def shared(cls, model_name: str = DEFAULT_MODEL_ID) -> "NeuralEmbedder":
        with cls._lock:
            inst = cls._instances.get(model_name)
            if inst is None:
                inst = cls(model_name=model_name)
                cls._instances[model_name] = inst
            return inst

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        with self._model_lock:
            if self._model is not None:
                return self._model
            from fastembed import TextEmbedding
            kwargs = {"model_name": self.model_name}
            if self.cache_dir:
                kwargs["cache_dir"] = self.cache_dir
            self._model = TextEmbedding(**kwargs)
            return self._model

    def warm_sync(self) -> None:
        self._ensure_model()

    def embed_passages_sync(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dims), dtype=np.float32)
        model = self._ensure_model()
        clipped = [t[:1500] for t in texts]
        vectors = list(model.embed(clipped))
        mat = np.asarray(vectors, dtype=np.float32)
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms = np.where(norms > 0.0, norms, 1.0)
        return mat / norms

    def embed_query_sync(self, text: str) -> np.ndarray:
        model = self._ensure_model()
        vec = next(iter(model.embed([text[:1500]])))
        arr = np.asarray(vec, dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(arr))
        return (arr / norm) if norm > 0.0 else arr

    async def embed_passages(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dims), dtype=np.float32)
        return await asyncio.to_thread(self.embed_passages_sync, texts)

    async def embed_query(self, text: str) -> np.ndarray:
        return await asyncio.to_thread(self.embed_query_sync, text)


def rrf_fuse(
    semantic_ranked: list[tuple[object, float]],
    lexical_ranked: list[tuple[object, float]],
    *,
    k: float = 40.0,
    w_sem: float = 1.0,
    w_lex: float = 0.75,
) -> dict[object, float]:
    """Reciprocal Rank Fusion over semantic and lexical candidate rankings."""
    fused: dict[object, float] = {}
    for rank, (key, _) in enumerate(semantic_ranked, start=1):
        fused[key] = fused.get(key, 0.0) + w_sem / (k + rank)
    for rank, (key, _) in enumerate(lexical_ranked, start=1):
        fused[key] = fused.get(key, 0.0) + w_lex / (k + rank)
    return fused


def mmr_order(
    candidates: list[tuple[object, float, np.ndarray | None]],
    *,
    limit: int = 32,
    lambda_mult: float = 0.75,
) -> list[object]:
    """Maximal Marginal Relevance ordering to avoid near-duplicate saturation.

    `candidates` is a list of `(key, relevance_score, normalized_vector_or_None)`.
    Scores are min-max normalized to [0, 1] so `lambda_mult` balances relevance
    against cosine redundancy consistently regardless of score scale.
    """
    if len(candidates) <= 1:
        return [c[0] for c in candidates[:limit]]
    scores = [max(0.0, float(c[1])) for c in candidates]
    hi = max(scores)
    norm_scores = [(s / hi) if hi > 0.0 else 0.0 for s in scores]

    remaining = list(range(len(candidates)))
    selected_indices: list[int] = []
    selected_vecs: list[np.ndarray] = []

    while remaining and len(selected_indices) < limit:
        best_pos = 0
        best_val = -1e9
        for pos, idx in enumerate(remaining):
            rel = norm_scores[idx]
            vec = candidates[idx][2]
            if selected_vecs and vec is not None:
                max_sim = max(float(np.dot(vec, sv)) for sv in selected_vecs)
            else:
                max_sim = 0.0
            val = lambda_mult * rel - (1.0 - lambda_mult) * max_sim
            if val > best_val:
                best_val = val
                best_pos = pos
        chosen = remaining.pop(best_pos)
        selected_indices.append(chosen)
        if candidates[chosen][2] is not None:
            selected_vecs.append(candidates[chosen][2])
    return [candidates[i][0] for i in selected_indices]


class _MatrixBucket:
    """In-memory normalized float32 matrix for one `(user_id, target_kind)`."""

    __slots__ = ("keys", "key_to_idx", "hashes", "matrix", "loaded")

    def __init__(self, dims: int = DEFAULT_DIMS):
        self.keys: list[tuple[int, int]] = []
        self.key_to_idx: dict[tuple[int, int], int] = {}
        self.hashes: dict[tuple[int, int], str] = {}
        self.matrix: np.ndarray = np.zeros((0, dims), dtype=np.float32)
        self.loaded: bool = False

    def rebuild(self, rows: list[tuple[tuple[int, int], str, np.ndarray]], dims: int = DEFAULT_DIMS) -> None:
        self.keys = [k for k, _, _ in rows]
        self.key_to_idx = {k: i for i, k in enumerate(self.keys)}
        self.hashes = {k: h for k, h, _ in rows}
        self.matrix = (
            np.vstack([v for _, _, v in rows]).astype(np.float32)
            if rows
            else np.zeros((0, dims), dtype=np.float32)
        )
        self.loaded = True

    def upsert(self, key: tuple[int, int], content_hash: str, vec: np.ndarray) -> None:
        vec = np.asarray(vec, dtype=np.float32).reshape(1, -1)
        idx = self.key_to_idx.get(key)
        if idx is not None:
            self.matrix[idx] = vec[0]
            self.hashes[key] = content_hash
            return
        self.key_to_idx[key] = len(self.keys)
        self.keys.append(key)
        self.hashes[key] = content_hash
        self.matrix = np.vstack([self.matrix, vec]) if self.matrix.shape[0] else vec

    def remove_target(self, target_id: int, *, min_section: int = 0) -> None:
        keep = [
            i for i, (tid, sidx) in enumerate(self.keys)
            if not (tid == target_id and sidx >= min_section)
        ]
        if len(keep) == len(self.keys):
            return
        self.keys = [self.keys[i] for i in keep]
        self.key_to_idx = {k: i for i, k in enumerate(self.keys)}
        self.hashes = {k: self.hashes[k] for k in self.keys}
        dims = self.matrix.shape[1] if self.matrix.ndim == 2 else DEFAULT_DIMS
        self.matrix = self.matrix[keep] if keep else np.zeros((0, dims), dtype=np.float32)

    def get_vector(self, key: tuple[int, int]) -> np.ndarray | None:
        idx = self.key_to_idx.get(key)
        if idx is None or idx >= self.matrix.shape[0]:
            return None
        return self.matrix[idx]

    def search(self, query_vec: np.ndarray, *, limit: int = 40, min_score: float = 0.25) -> list[tuple[tuple[int, int], float]]:
        if not self.keys or self.matrix.shape[0] == 0:
            return []
        scores = self.matrix @ query_vec
        if scores.size <= limit:
            order = np.argsort(-scores)
        else:
            part = np.argpartition(-scores, limit)[:limit]
            order = part[np.argsort(-scores[part])]
        out = []
        for idx in order:
            sc = float(scores[idx])
            if sc < min_score:
                break
            out.append((self.keys[int(idx)], sc))
        return out


class SemanticIndex:
    """Persistent incremental multilingual vector index for Mark VI memory."""

    def __init__(
        self,
        session_factory,
        *,
        embedder: NeuralEmbedder | None = None,
        enabled: bool | None = None,
    ):
        self.sessions = session_factory
        self.embedder = embedder or NeuralEmbedder.shared()
        self.model_id = self.embedder.model_name
        self.dims = self.embedder.dims
        if enabled is None:
            enabled = os.getenv("SPEDA_SEMANTIC_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
        self.enabled = enabled
        self._buckets: dict[tuple[int, str], _MatrixBucket] = {}
        self._lock = asyncio.Lock()

    def _bucket(self, user_id: int, target_kind: str) -> _MatrixBucket:
        key = (user_id, target_kind)
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _MatrixBucket(self.dims)
            self._buckets[key] = bucket
        return bucket

    def evict_user(self, user_id: int) -> None:
        """Immediately evict all in-memory vector buckets for user_id."""
        for key in [k for k in self._buckets if k[0] == user_id]:
            self._buckets.pop(key, None)

    async def warm(self) -> None:
        if not self.enabled:
            return
        try:
            await asyncio.to_thread(self.embedder.warm_sync)
        except Exception:
            logger.exception("semantic_embedder_warm_failed")

    async def _ensure_loaded(self, db, user_id: int, target_kind: str) -> _MatrixBucket:
        bucket = self._bucket(user_id, target_kind)
        if bucket.loaded:
            return bucket
        rows = (await db.execute(select(MemoryEmbedding).where(
            MemoryEmbedding.user_id == user_id,
            MemoryEmbedding.target_kind == target_kind,
            MemoryEmbedding.model_id == self.model_id,
            MemoryEmbedding.dims == self.dims,
        ))).scalars().all()
        parsed = []
        for row in rows:
            try:
                parsed.append(((row.target_id, row.section_idx), row.content_hash, unpack_vector(row.vector, self.dims)))
            except ValueError:
                continue
        bucket.rebuild(parsed, self.dims)
        return bucket

    @staticmethod
    async def _upsert_rows(db, values: list[dict]) -> None:
        if not values:
            return
        factory = sqlite_insert if db.bind.dialect.name == "sqlite" else pg_insert
        for batch_start in range(0, len(values), 128):
            chunk = values[batch_start:batch_start + 128]
            stmt = factory(MemoryEmbedding).values(chunk)
            stmt = stmt.on_conflict_do_update(
                index_elements=["user_id", "target_kind", "target_id", "section_idx"],
                set_={
                    "content_hash": stmt.excluded.content_hash,
                    "model_id": stmt.excluded.model_id,
                    "dims": stmt.excluded.dims,
                    "vector": stmt.excluded.vector,
                    "updated_at": stmt.excluded.updated_at,
                },
            )
            await db.execute(stmt)

    async def sync_unit_sections(self, db, user_id: int, unit: MemoryServingUnit) -> int:
        """Synchronize embeddings for one serving unit (handles create, update, section shrink)."""
        if not self.enabled:
            return 0
        bucket = await self._ensure_loaded(db, user_id, "unit")
        sections = unit.sections or []
        to_embed: list[tuple[int, str, str]] = []
        for sidx, sec in enumerate(sections):
            passage = _unit_passage_text(unit.title, unit.aliases, sec.get("text", ""))
            if not passage.strip():
                continue
            chash = _digest(f"{self.model_id}:{passage}")
            if bucket.hashes.get((unit.id, sidx)) != chash:
                to_embed.append((sidx, chash, passage))
        # Remove embeddings for deleted tail sections when a unit shrinks
        await db.execute(delete(MemoryEmbedding).where(
            MemoryEmbedding.user_id == user_id,
            MemoryEmbedding.target_kind == "unit",
            MemoryEmbedding.target_id == unit.id,
            MemoryEmbedding.section_idx >= len(sections),
        ))
        bucket.remove_target(unit.id, min_section=len(sections))
        if not to_embed:
            return 0
        vecs = await self.embedder.embed_passages([p for _, _, p in to_embed])
        now = datetime.now(timezone.utc)
        rows = []
        for (sidx, chash, _), vec in zip(to_embed, vecs):
            rows.append({
                "user_id": user_id,
                "target_kind": "unit",
                "target_id": unit.id,
                "section_idx": sidx,
                "content_hash": chash,
                "model_id": self.model_id,
                "dims": self.dims,
                "vector": pack_vector(vec),
                "updated_at": now,
            })
            bucket.upsert((unit.id, sidx), chash, vec)
        await self._upsert_rows(db, rows)
        return len(rows)

    async def delete_unit(self, db, user_id: int, unit_id: int) -> None:
        if not self.enabled:
            return
        await db.execute(delete(MemoryEmbedding).where(
            MemoryEmbedding.user_id == user_id,
            MemoryEmbedding.target_kind == "unit",
            MemoryEmbedding.target_id == unit_id,
        ))
        self._bucket(user_id, "unit").remove_target(unit_id)

    async def delete_source(self, db, user_id: int, source_id: int) -> None:
        if not self.enabled:
            return
        await db.execute(delete(MemoryEmbedding).where(
            MemoryEmbedding.user_id == user_id,
            MemoryEmbedding.target_kind == "source",
            MemoryEmbedding.target_id == source_id,
        ))
        self._bucket(user_id, "source").remove_target(source_id)

    async def sync_units_for_user(self, db, user_id: int, *, max_new: int = 256) -> int:
        """Lazily sync any unindexed, updated, deleted, or model-version-changed serving units."""
        if not self.enabled:
            return 0
        bucket = await self._ensure_loaded(db, user_id, "unit")
        units = (await db.execute(select(MemoryServingUnit).where(
            MemoryServingUnit.user_id == user_id
        ))).scalars().all()
        live_ids = {u.id for u in units}
        stale_ids = {tid for tid, _ in bucket.keys if tid not in live_ids}
        if stale_ids:
            await db.execute(delete(MemoryEmbedding).where(
                MemoryEmbedding.user_id == user_id,
                MemoryEmbedding.target_kind == "unit",
                MemoryEmbedding.target_id.in_(stale_ids),
            ))
            for tid in stale_ids:
                bucket.remove_target(tid)
        # Drop any stale model-version rows for this user
        await db.execute(delete(MemoryEmbedding).where(
            MemoryEmbedding.user_id == user_id,
            MemoryEmbedding.target_kind == "unit",
            MemoryEmbedding.model_id != self.model_id,
        ))
        pending: list[tuple[int, int, str, str]] = []
        for u in units:
            sections = u.sections or []
            bucket.remove_target(u.id, min_section=len(sections))
            for sidx, sec in enumerate(sections):
                passage = _unit_passage_text(u.title, u.aliases, sec.get("text", ""))
                if not passage.strip():
                    continue
                chash = _digest(f"{self.model_id}:{passage}")
                if bucket.hashes.get((u.id, sidx)) != chash:
                    pending.append((u.id, sidx, chash, passage))
                    if len(pending) >= max_new:
                        break
            if len(pending) >= max_new:
                break
        if not pending:
            return 0
        vecs = await self.embedder.embed_passages([p for _, _, _, p in pending])
        now = datetime.now(timezone.utc)
        rows = []
        for (uid, sidx, chash, _), vec in zip(pending, vecs):
            rows.append({
                "user_id": user_id,
                "target_kind": "unit",
                "target_id": uid,
                "section_idx": sidx,
                "content_hash": chash,
                "model_id": self.model_id,
                "dims": self.dims,
                "vector": pack_vector(vec),
                "updated_at": now,
            })
            bucket.upsert((uid, sidx), chash, vec)
        await self._upsert_rows(db, rows)
        return len(rows)

    async def sync_recent_sources(self, db, user_id: int, *, scan_limit: int = 300, max_new: int = 64) -> int:
        """Incrementally embed recent active owner sources and purge revoked ones."""
        if not self.enabled:
            return 0
        bucket = await self._ensure_loaded(db, user_id, "source")
        sources = (await db.execute(select(MemoryChange).where(
            MemoryChange.user_id == user_id,
            MemoryChange.source_role == "owner",
            MemoryChange.revoked.is_(False),
        ).order_by(MemoryChange.id.desc()).limit(scan_limit))).scalars().all()
        pending: list[tuple[int, str, str]] = []
        for src in sources:
            if not (src.content or "").strip() or (src.provenance or {}).get("iacon_record"):
                continue
            passage = _source_passage_text(src)
            chash = _digest(f"{self.model_id}:{passage}")
            if bucket.hashes.get((src.id, 0)) != chash:
                pending.append((src.id, chash, passage))
                if len(pending) >= max_new:
                    break
        if not pending:
            return 0
        vecs = await self.embedder.embed_passages([p for _, _, p in pending])
        now = datetime.now(timezone.utc)
        rows = []
        for (sid, chash, _), vec in zip(pending, vecs):
            rows.append({
                "user_id": user_id,
                "target_kind": "source",
                "target_id": sid,
                "section_idx": 0,
                "content_hash": chash,
                "model_id": self.model_id,
                "dims": self.dims,
                "vector": pack_vector(vec),
                "updated_at": now,
            })
            bucket.upsert((sid, 0), chash, vec)
        await self._upsert_rows(db, rows)
        return len(rows)

    async def embed_query(self, query: str) -> np.ndarray | None:
        if not self.enabled or not (query or "").strip():
            return None
        try:
            return await self.embedder.embed_query(query)
        except Exception:
            logger.exception("semantic_query_embed_failed")
            return None

    async def search_units(
        self,
        db,
        user_id: int,
        query_vec: np.ndarray,
        *,
        limit: int = 32,
        min_score: float = 0.25,
    ) -> list[tuple[int, int, float]]:
        if not self.enabled or query_vec is None:
            return []
        async with self._lock:
            await self.sync_units_for_user(db, user_id, max_new=64)
            bucket = await self._ensure_loaded(db, user_id, "unit")
            hits = bucket.search(query_vec, limit=limit, min_score=min_score)
        return [(tid, sidx, sc) for (tid, sidx), sc in hits]

    async def search_sources(
        self,
        db,
        user_id: int,
        query_vec: np.ndarray,
        *,
        limit: int = 32,
        min_score: float = 0.25,
    ) -> list[tuple[int, float]]:
        if not self.enabled or query_vec is None:
            return []
        async with self._lock:
            await self.sync_recent_sources(db, user_id, scan_limit=250, max_new=32)
            bucket = await self._ensure_loaded(db, user_id, "source")
            hits = bucket.search(query_vec, limit=limit, min_score=min_score)
        return [(tid, sc) for (tid, _), sc in hits]

    def get_unit_vector(self, user_id: int, unit_id: int, section_idx: int) -> np.ndarray | None:
        return self._bucket(user_id, "unit").get_vector((unit_id, section_idx))

    def get_source_vector(self, user_id: int, source_id: int) -> np.ndarray | None:
        return self._bucket(user_id, "source").get_vector((source_id, 0))
