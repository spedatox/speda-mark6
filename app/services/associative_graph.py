"""Sparse bi-temporal associative graph and Personalized PageRank (PPR) engine.

Uses O(V + E) NumPy sparse edge arrays (zero dense N x N matrices) with:
- Strict bi-temporal filtering (Timeline T valid_from/valid_until + Timeline T' superseded_at)
  instead of arbitrary freshness decay penalties.
- Epistemic differentiation (explicit vs. inferred edges).
- Deterministic shortest-chain reconstruction for multi-hop provenance.
- Lightweight sparse label-propagation community detection for Orion Dreaming chapters.
"""
from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
import logging
import os
import re
from typing import Literal

import numpy as np
from sqlalchemy import select

from app.models.iacon import IaconEdge, IaconEntityAlias, IaconRecord
from app.services.memory_store import fold

logger = logging.getLogger(__name__)

_ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_ISO_YEAR_MONTH_RE = re.compile(r"\b(20\d{2})-(0[1-9]|1[0-2])\b")

INF_TS = float("inf")
NEG_INF_TS = float("-inf")


def _to_epoch_ts(dt: datetime | date | None, *, default: float) -> float:
    if dt is None:
        return default
    if isinstance(dt, date) and not isinstance(dt, datetime):
        dt = datetime.combine(dt, time.min, timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


@dataclass(frozen=True)
class TemporalScope:
    """Explicit bi-temporal filter for graph traversal (no arbitrary decay penalties)."""
    mode: Literal["current", "as_of", "range", "biographical"] = "biographical"
    as_of_ts: float = INF_TS
    range_start_ts: float = NEG_INF_TS
    range_end_ts: float = INF_TS

    @classmethod
    def current(cls, today: date | None = None) -> "TemporalScope":
        d = today or datetime.now(timezone.utc).date()
        end_of_day = datetime.combine(d, time.max, timezone.utc).timestamp()
        return cls(mode="current", as_of_ts=end_of_day)

    @classmethod
    def at_date(cls, target: date) -> "TemporalScope":
        end_of_day = datetime.combine(target, time.max, timezone.utc).timestamp()
        return cls(mode="as_of", as_of_ts=end_of_day)

    @classmethod
    def between(cls, start: date, end: date) -> "TemporalScope":
        s_ts = datetime.combine(start, time.min, timezone.utc).timestamp()
        e_ts = datetime.combine(end, time.max, timezone.utc).timestamp()
        return cls(mode="range", range_start_ts=s_ts, range_end_ts=e_ts)

    @classmethod
    def biographical(cls, today: date | None = None) -> "TemporalScope":
        d = today or datetime.now(timezone.utc).date()
        end_of_day = datetime.combine(d, time.max, timezone.utc).timestamp()
        return cls(mode="biographical", as_of_ts=end_of_day)


def parse_structural_temporal_scope(query: str, *, today: date, default_mode: Literal["current", "biographical"] = "biographical") -> TemporalScope:
    """Parse explicit ISO-8601 date bounds from a query (purely structural, no semantic regex)."""
    dates: list[date] = []
    for raw in _ISO_DATE_RE.findall(query or ""):
        try:
            dates.append(date.fromisoformat(raw))
        except ValueError:
            pass
    if len(dates) >= 2:
        lo, hi = min(dates), max(dates)
        return TemporalScope.between(lo, hi)
    if len(dates) == 1:
        return TemporalScope.at_date(dates[0])
    ym = _ISO_YEAR_MONTH_RE.findall(query or "")
    if ym:
        y, m = int(ym[0][0]), int(ym[0][1])
        start = date(y, m, 1)
        end = date(y + (1 if m == 12 else 0), 1 if m == 12 else m + 1, 1)
        return TemporalScope.between(start, end)
    return TemporalScope.current(today) if default_mode == "current" else TemporalScope.biographical(today)


@dataclass
class EdgeRecord:
    edge_id: str
    source_id: str
    target_id: str
    relation: str
    description: str
    epistemic_mode: str
    confidence: float
    valid_from: datetime | None
    valid_until: datetime | None
    superseded_at: datetime | None
    status: str


@dataclass
class SparseUserGraph:
    """O(V + E) sparse graph representation cached per (user_id, epoch)."""
    user_id: int
    epoch: int
    node_ids: list[str]
    node_to_idx: dict[str, int]
    node_titles: dict[str, str]
    node_kinds: dict[str, str]
    # Bidirectional sparse directed edges (each stored edge adds forward + reverse traversal entries)
    src_idx: np.ndarray          # int32 [2*E]
    dst_idx: np.ndarray          # int32 [2*E]
    base_weight: np.ndarray      # float32 [2*E] (confidence * epistemic_factor)
    valid_from_ts: np.ndarray    # float64 [2*E]
    valid_until_ts: np.ndarray   # float64 [2*E]
    superseded_ts: np.ndarray    # float64 [2*E] (INF_TS if not superseded)
    edge_meta_idx: np.ndarray    # int32 [2*E] -> index into `edges`
    edges: list[EdgeRecord] = field(default_factory=list)
    # Adjacency list for fast O(deg) path reconstruction: node_idx -> list[(neighbor_idx, edge_pos_in_arrays)]
    adj: list[list[tuple[int, int]]] = field(default_factory=list)
    # Alias lookup: folded_alias -> set[record_id]
    aliases: dict[str, set[str]] = field(default_factory=dict)

    @property
    def num_nodes(self) -> int:
        return len(self.node_ids)

    @property
    def num_edges(self) -> int:
        return len(self.edges)

    def edge_mask(self, scope: TemporalScope) -> np.ndarray:
        """Compute a boolean mask over the sparse edge arrays using strict bi-temporal validity."""
        if self.src_idx.size == 0:
            return np.zeros(0, dtype=bool)
        if scope.mode == "current":
            # Strictly valid now and not superseded
            return (
                (self.superseded_ts == INF_TS)
                & (self.valid_from_ts <= scope.as_of_ts)
                & (self.valid_until_ts > scope.as_of_ts)
            )
        if scope.mode == "as_of":
            # Point-in-time world truth: valid at as_of_ts (even if later superseded in T')
            return (self.valid_from_ts <= scope.as_of_ts) & (self.valid_until_ts > scope.as_of_ts)
        if scope.mode == "range":
            # Any edge whose validity interval overlaps [range_start_ts, range_end_ts]
            return (self.valid_from_ts <= scope.range_end_ts) & (self.valid_until_ts >= scope.range_start_ts)
        # "biographical": full non-deleted timeline up to as_of_ts (preserves historical milestones)
        return self.valid_from_ts <= scope.as_of_ts

    def personalized_pagerank(
        self,
        seed_weights: dict[str, float],
        *,
        scope: TemporalScope,
        alpha: float = 0.25,
        iterations: int = 15,
        top_k: int = 16,
    ) -> list[tuple[str, float]]:
        """O(T * E) sparse Personalized PageRank over temporally filtered edges."""
        n = self.num_nodes
        if n == 0 or not seed_weights:
            return []

        v = np.zeros(n, dtype=np.float64)
        for rid, w in seed_weights.items():
            idx = self.node_to_idx.get(rid)
            if idx is not None and w > 0.0:
                v[idx] += float(w)
        total_v = float(v.sum())
        if total_v <= 0.0:
            return []
        v /= total_v

        mask = self.edge_mask(scope)
        active_src = self.src_idx[mask]
        active_dst = self.dst_idx[mask]
        active_w = self.base_weight[mask].astype(np.float64)

        if active_src.size == 0:
            order = np.argsort(-v)[:top_k]
            return [(self.node_ids[int(i)], float(v[i])) for i in order if v[i] > 0.0]

        # Row-normalize outgoing weights per source node: O(E)
        out_sum = np.bincount(active_src, weights=active_w, minlength=n)
        norm_w = active_w / np.where(out_sum[active_src] > 0.0, out_sum[active_src], 1.0)
        dangling_mask = out_sum == 0.0

        p = v.copy()
        one_minus_alpha = 1.0 - alpha
        for _ in range(iterations):
            dangling_mass = float(p[dangling_mask].sum()) if np.any(dangling_mask) else 0.0
            propagated = np.bincount(active_dst, weights=p[active_src] * norm_w, minlength=n)
            p_next = alpha * v + one_minus_alpha * (propagated + dangling_mass * v)
            if float(np.abs(p_next - p).sum()) < 1e-6:
                p = p_next
                break
            p = p_next

        if n <= top_k:
            order = np.argsort(-p)
        else:
            part = np.argpartition(-p, top_k)[:top_k]
            order = part[np.argsort(-p[part])]
        return [(self.node_ids[int(i)], float(p[i])) for i in order if p[i] > 1e-6]

    def extract_chains(
        self,
        seed_ids: list[str],
        target_ids: list[str],
        *,
        scope: TemporalScope,
        max_hops: int = 3,
        max_chains: int = 8,
    ) -> list[list[EdgeRecord]]:
        """Reconstruct shortest temporally valid associative chains between seeds and PPR-reached nodes."""
        if not seed_ids or not target_ids or self.src_idx.size == 0:
            return []
        mask = self.edge_mask(scope)
        seed_set = {self.node_to_idx[s] for s in seed_ids if s in self.node_to_idx}
        target_set = {self.node_to_idx[t] for t in target_ids if t in self.node_to_idx}
        if not seed_set:
            return []

        chains: list[list[EdgeRecord]] = []
        seen_edge_tuples: set[tuple[str, ...]] = set()

        for s_idx in seed_set:
            # BFS up to max_hops
            queue: deque[tuple[int, list[EdgeRecord]]] = deque([(s_idx, [])])
            visited: dict[int, int] = {s_idx: 0}
            while queue and len(chains) < max_chains:
                curr, path = queue.popleft()
                if len(path) >= max_hops:
                    continue
                for nbr, arr_pos in self.adj[curr]:
                    if not mask[arr_pos]:
                        continue
                    edge = self.edges[int(self.edge_meta_idx[arr_pos])]
                    new_path = [*path, edge]
                    if nbr in target_set and nbr != s_idx:
                        sig = tuple(e.edge_id for e in new_path)
                        if sig not in seen_edge_tuples:
                            seen_edge_tuples.add(sig)
                            chains.append(new_path)
                            if len(chains) >= max_chains:
                                break
                    if nbr not in visited or visited[nbr] > len(new_path):
                        visited[nbr] = len(new_path)
                        queue.append((nbr, new_path))
        # Sort chains chronologically by earliest edge valid_from
        chains.sort(key=lambda ch: min((_to_epoch_ts(e.valid_from, default=INF_TS) for e in ch), default=INF_TS))
        return chains

    def detect_communities(self, *, max_iters: int = 12) -> dict[int, list[str]]:
        """Deterministic sparse label propagation to discover Life-Chapter communities."""
        n = self.num_nodes
        if n == 0:
            return {}
        labels = list(range(n))
        for _ in range(max_iters):
            changed = False
            for u in range(n):
                if not self.adj[u]:
                    continue
                counts: dict[int, float] = {}
                for nbr, arr_pos in self.adj[u]:
                    lbl = labels[nbr]
                    counts[lbl] = counts.get(lbl, 0.0) + float(self.base_weight[arr_pos])
                if not counts:
                    continue
                # Deterministic tie-break on smallest label id
                best_lbl = min(counts.keys(), key=lambda k: (-counts[k], k))
                if best_lbl != labels[u]:
                    labels[u] = best_lbl
                    changed = True
            if not changed:
                break
        groups: dict[int, list[str]] = {}
        for idx, lbl in enumerate(labels):
            groups.setdefault(lbl, []).append(self.node_ids[idx])
        return groups

    def match_aliases(self, query_text: str) -> set[str]:
        """Exact Unicode-folded alias token/phrase match (pure lexical entity resolution)."""
        if not self.aliases or not query_text:
            return set()
        folded = " " + fold(query_text) + " "
        matched: set[str] = set()
        for alias_folded, rids in self.aliases.items():
            if len(alias_folded) < 2:
                continue
            if f" {alias_folded} " in folded:
                matched.update(rids)
        return matched


class AssociativeGraphIndex:
    """Epoch-fenced sparse graph cache per user_id with deterministic eviction."""

    def __init__(self, *, enabled: bool | None = None, max_users: int = 16):
        if enabled is None:
            enabled = os.getenv("SPEDA_ASSOCIATIVE_PPR", "true").strip().lower() not in {"0", "false", "no", "off"}
        self.enabled = enabled
        self.max_users = max_users
        self._cache: OrderedDict[tuple[int, int], SparseUserGraph] = OrderedDict()

    def invalidate_user(self, user_id: int) -> None:
        """Immediately evict all cached sparse graphs for user_id (deletion/update propagation)."""
        stale_keys = [k for k in self._cache if k[0] == user_id]
        for k in stale_keys:
            self._cache.pop(k, None)

    async def load(self, db, user_id: int, epoch: int) -> SparseUserGraph:
        key = (user_id, epoch)
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return cached

        # Evict older epochs for this user immediately
        self.invalidate_user(user_id)

        records = (await db.execute(select(
            IaconRecord.id, IaconRecord.title, IaconRecord.kind
        ).where(
            IaconRecord.user_id == user_id,
            IaconRecord.status == "accepted",
        ))).all()

        node_ids: list[str] = []
        node_to_idx: dict[str, int] = {}
        node_titles: dict[str, str] = {}
        node_kinds: dict[str, str] = {}
        for rid, title, kind in records:
            node_to_idx[rid] = len(node_ids)
            node_ids.append(rid)
            node_titles[rid] = title
            node_kinds[rid] = kind

        raw_edges = (await db.execute(select(IaconEdge).where(
            IaconEdge.user_id == user_id,
            IaconEdge.status.in_(("accepted", "superseded", "contested")),
        ))).scalars().all()

        edges: list[EdgeRecord] = []
        src_list: list[int] = []
        dst_list: list[int] = []
        w_list: list[float] = []
        vf_list: list[float] = []
        vu_list: list[float] = []
        sup_list: list[float] = []
        meta_list: list[int] = []
        adj: list[list[tuple[int, int]]] = [[] for _ in range(len(node_ids))]

        for row in raw_edges:
            u = node_to_idx.get(row.source_id)
            v = node_to_idx.get(row.target_id)
            if u is None or v is None or u == v:
                continue
            e_idx = len(edges)
            edges.append(EdgeRecord(
                edge_id=row.id,
                source_id=row.source_id,
                target_id=row.target_id,
                relation=row.relation,
                description=row.description or "",
                epistemic_mode=row.epistemic_mode or "explicit",
                confidence=float(row.confidence if row.confidence is not None else 1.0),
                valid_from=row.valid_from,
                valid_until=row.valid_until,
                superseded_at=row.superseded_at,
                status=row.status,
            ))
            epistemic_factor = 1.0 if row.epistemic_mode == "explicit" else 0.70
            base_w = max(0.05, float(row.confidence if row.confidence is not None else 1.0) * epistemic_factor)
            vf = _to_epoch_ts(row.valid_from, default=NEG_INF_TS)
            vu = _to_epoch_ts(row.valid_until, default=INF_TS)
            sup = _to_epoch_ts(row.superseded_at, default=INF_TS)

            # Add both forward (u -> v) and reverse (v -> u, slightly attenuated 0.85) for associative traversal
            pos_fwd = len(src_list)
            src_list.append(u)
            dst_list.append(v)
            w_list.append(base_w)
            vf_list.append(vf)
            vu_list.append(vu)
            sup_list.append(sup)
            meta_list.append(e_idx)
            adj[u].append((v, pos_fwd))

            pos_rev = len(src_list)
            src_list.append(v)
            dst_list.append(u)
            w_list.append(base_w * 0.85)
            vf_list.append(vf)
            vu_list.append(vu)
            sup_list.append(sup)
            meta_list.append(e_idx)
            adj[v].append((u, pos_rev))

        alias_rows = (await db.execute(select(
            IaconEntityAlias.alias_folded, IaconEntityAlias.record_id
        ).where(IaconEntityAlias.user_id == user_id))).all()
        aliases: dict[str, set[str]] = {}
        for af, rid in alias_rows:
            if rid in node_to_idx and af:
                aliases.setdefault(af, set()).add(rid)
        # Also include folded record titles as natural aliases
        for rid, title in node_titles.items():
            ft = fold(title).strip()
            if len(ft) >= 2:
                aliases.setdefault(ft, set()).add(rid)

        graph = SparseUserGraph(
            user_id=user_id,
            epoch=epoch,
            node_ids=node_ids,
            node_to_idx=node_to_idx,
            node_titles=node_titles,
            node_kinds=node_kinds,
            src_idx=np.asarray(src_list, dtype=np.int32),
            dst_idx=np.asarray(dst_list, dtype=np.int32),
            base_weight=np.asarray(w_list, dtype=np.float32),
            valid_from_ts=np.asarray(vf_list, dtype=np.float64),
            valid_until_ts=np.asarray(vu_list, dtype=np.float64),
            superseded_ts=np.asarray(sup_list, dtype=np.float64),
            edge_meta_idx=np.asarray(meta_list, dtype=np.int32),
            edges=edges,
            adj=adj,
            aliases=aliases,
        )
        self._cache[key] = graph
        while len(self._cache) > self.max_users:
            self._cache.popitem(last=False)
        return graph
