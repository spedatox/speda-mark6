# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Semantic (meaning-based) search over the owner's entire conversation history.

Complements the keyword-only `search_history` skill: that one finds exact
phrases, this one finds relevant past exchanges even when the wording differs
entirely from how the query is phrased. Retrieval is HYBRID: a brute-force cosine pass over the L2-normalized
MessageEmbedding vectors (app/models/message_embedding.py — see
app/services/embeddings.py for why brute force is the right call at single-user
scale) fused by Reciprocal Rank Fusion with a BM25 pass over an FTS5 index of
the same messages. The vector half finds the conversation you can only describe;
the keyword half finds the one you can name. Fusing them is what stops a rare
literal token — a course code, a person, an amount — from being outranked by
prose that merely sounds similar. See app/services/lexical.py. Both indexes are
populated incrementally every turn and backfilled via POST /admin/index-embeddings
(app/services/embedding_indexer.py).

Three retrieval properties are ported from Honcho's message tools
(plastic-labs/honcho, `search_messages` / `search_messages_temporal`), because
without them a semantic hit is frequently unusable:

  - **Surrounding turns.** A matched message is shown with the turns either side
    of it. A bare snippet strips the exchange that gives it meaning: "yes, do
    that" ranks well against half the queries the owner asks and answers none of
    them alone.
  - **Merged neighbours.** Hits that land close together in one session are
    rendered as ONE snippet instead of three overlapping ones, so the result
    budget buys distinct exchanges rather than the same exchange three times.
  - **Date filtering on the semantic pass.** Meaning and time are one query, not
    two. "What did we most recently decide about X" needs both halves at once;
    running them separately answers neither.
"""

import asyncio
import logging
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

import numpy as np
from sqlalchemy import select

from app.core.context import AgentContext
from app.models.message import Message
from app.models.message_embedding import MessageEmbedding
from app.models.session import Session
from app.services import lexical
from app.services.embeddings import embed_texts
from app.skills.base import Skill

logger = logging.getLogger(__name__)


async def _vector_watermark(db, user_id: int) -> tuple:
    """A cheap fingerprint of this owner's embedded messages.

    Count plus the highest id plus the newest `created_at`: between them these
    move on every insert and every backfill, and it is one indexed aggregate
    rather than a scan. Mirrors observations._vector_watermark.
    """
    from sqlalchemy import func

    row = (
        await db.execute(
            select(
                func.count(MessageEmbedding.id),
                func.max(MessageEmbedding.id),
                func.max(MessageEmbedding.created_at),
            ).where(MessageEmbedding.user_id == user_id)
        )
    ).one()
    return (row[0], row[1], str(row[2]))


async def _vectors_for(db, user_id: int, cache=None) -> tuple[dict[int, int], "np.ndarray"]:
    """`({message_embedding_id: row index}, matrix)` for this owner, cached.

    The matrix is contiguous and L2-normalized (app/services/embeddings.py), so
    scoring the whole corpus is one `matrix @ query` — cheaper than gathering
    the filtered subset would be, and it lets the caller pick out the rows that
    survived SQL filtering by plain lookup.
    """
    watermark = await _vector_watermark(db, user_id)
    cache = _VECTOR_CACHE if cache is None else cache
    # Include the database identity: isolated evaluations and multiple stores
    # must never share vectors merely because their owner ids coincide.
    key = (id(db.get_bind()), user_id) if cache is not _VECTOR_CACHE else user_id
    cached = cache.get(key)
    if cached is not None and cached[0] == watermark:
        return cached[1], cached[2]

    statement = select(MessageEmbedding.id, MessageEmbedding.embedding).where(MessageEmbedding.user_id == user_id)
    append_only = bool(cached and cached[0][1] is not None
                       and watermark[0] > cached[0][0] and watermark[1] > cached[0][1])
    if append_only:
        statement = statement.where(MessageEmbedding.id > cached[0][1])
    rows = (await db.execute(statement)).all()
    if append_only and len(rows) != watermark[0] - cached[0][0]:
        rows = (await db.execute(select(MessageEmbedding.id, MessageEmbedding.embedding)
            .where(MessageEmbedding.user_id == user_id))).all()
        append_only = False
    usable = [(int(eid), blob) for eid, blob in rows if blob]
    if not usable:
        index: dict[int, int] = {}
        matrix = np.zeros((0, 0), dtype=np.float32)
    else:
        offset = len(cached[1]) if append_only else 0
        index = dict(cached[1]) if append_only else {}
        index.update({eid: offset + i for i, (eid, _) in enumerate(usable)})
        matrix = await asyncio.to_thread(lambda: np.stack([
            np.frombuffer(blob, dtype=np.float32) for _, blob in usable
        ]))
        if append_only and cached[2].size:
            matrix = await asyncio.to_thread(np.concatenate, (cached[2], matrix))
    cache[key] = (watermark, index, matrix)
    logger.info(
        "recall_vector_cache_filled",
        extra={"user_id": user_id, "rows": len(usable),
               "mb": round(matrix.nbytes / 1e6, 1)},
    )
    return index, matrix

MAX_CANDIDATES = 50_000   # perf guard on the brute-force scan; single-user scale
MAX_PER_SESSION = 3       # diversity cap so one conversation can't fill the list
SNIPPET_CHARS = 400       # per-message text budget inside a rendered snippet

# ── The vector cache ─────────────────────────────────────────────────────────
# The same optimisation app/services/observations.py documents for the fact
# tier, applied here for the same reason and at a much worse ratio. That one
# was fixed when 2,000 observations cost 12.3 MB and 227 ms; this path loads
# EVERY embedded message's BLOB through the ORM on every call, and on the live
# deployment that measured:
#
#     rows 20,635 · matrix 126.8 MB · DB load 6.05 s · stack 0.33 s · dot 0.01 s
#
# Six seconds of it is the BLOB read, and none of those bytes change between one
# recall and the next. Worse, it is all inside one request coroutine: uvicorn
# pings every WebSocket every 20 s and drops the ones that miss the deadline, so
# a few of these overlapping is enough to take out every connected client at
# once — which is exactly how multiple peer clients were dying on the same second.
#
# Held in process, keyed on a watermark cheap enough to check every time, and
# stored as ONE contiguous matrix rather than 20k separate buffers so scoring is
# a single BLAS call with no per-call gather. The FILTERING still happens in SQL
# — every session/date/agent predicate is evaluated by the database exactly as
# before — and the cache is consulted only to turn surviving rows into scores.
# The row query defers the `embedding` column, so on a hit the BLOBs are never
# read at all. A row embedded since the watermark simply sits out the vector
# pass and is still ranked by the lexical half, the same graceful degradation
# the two-retriever design already has for a row the embedder has not reached.
_VECTOR_CACHE: dict[int, tuple[tuple, dict[int, int], "np.ndarray"]] = {}


def warm_conversation_vectors(context, cache):
    """Refresh changed index rows in an app-owned task with its own DB reader."""
    from sqlalchemy.ext.asyncio import AsyncSession
    key = (id(context.db.get_bind()), context.user_id)
    pending = cache.conversation_warmups.get(key)
    if pending is None or pending.done():
        async def warm():
            async with AsyncSession(bind=context.db.bind) as reader:
                return await _vectors_for(reader, context.user_id, cache.conversation_vectors)
        pending = asyncio.create_task(warm())
        cache.conversation_warmups[key] = pending
        def observed(task):
            if not task.cancelled() and task.exception() is not None:
                logger.warning("conversation_vector_warmup_failed", extra={
                    "request_id": context.request_id, "error": type(task.exception()).__name__})
        pending.add_done_callback(observed)
    return pending


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _extract_text(content) -> str:
    """Pull plain text out of an Anthropic content block array (or string)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        return " ".join(p for p in parts if p)
    return ""


async def _session_message_order(db, session_id: int) -> list[int]:
    """Message ids of one session in conversation order.

    Ids are globally autoincrementing, so they ORDER a session correctly but are
    not contiguous within it — other sessions interleave. Neighbours therefore
    have to come from the session's own ordered list, not an id range.
    """
    rows = await db.execute(
        select(Message.id)
        .where(Message.session_id == session_id, Message.role.in_(("user", "assistant")))
        .order_by(Message.id.asc())
    )
    return [r[0] for r in rows.all()]


def _merge_windows(indices: list[int], context_window: int) -> list[tuple[int, int]]:
    """Collapse hit positions into merged [start, end] index spans.

    Two hits three turns apart share most of their context; rendering both in
    full spends the result budget on the same exchange twice.
    """
    spans = sorted(
        (max(0, i - context_window), i + context_window) for i in indices
    )
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


class SemanticSearchSkill(Skill):
    name = "recall_conversations"
    description = (
        "Search the owner's ENTIRE conversation history — across every agent, not just "
        "the current one — by MEANING rather than exact wording, optionally narrowed to a "
        "date range. Use this for conceptual or fuzzy recall: 'have we discussed X before', "
        "'what did I decide about Y', 'what was I saying about Z back in June', or "
        "synthesising something the owner mentioned that was never distilled into a "
        "/memories file. Do NOT retrieve history to answer a follow-up already resolved "
        "by the current conversation or available tool results. Do NOT use it for an "
        "exact phrase or keyword you already know the "
        "wording of — `search_history` is faster and more precise for literal matches — and "
        "do NOT use it to look up facts the roster has already distilled, which is "
        "`search_memory`. Returns matching exchanges as snippets that include the turns "
        "either side of each hit, grouped by conversation, each tagged with its session, "
        "originating agent, date and original message:<id> for source evidence."
    )
    read_only = True
    requires_network = True  # calls OpenAI to embed the query
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to recall, in natural language — a topic, question, or past decision.",
            },
            "after": {
                "type": "string",
                "description": (
                    "Optional: only exchanges on/after this date (YYYY-MM-DD). Combine with "
                    "the query to find the MOST RECENT discussion of a topic."
                ),
            },
            "before": {
                "type": "string",
                "description": "Optional: only exchanges on/before this date (YYYY-MM-DD).",
            },
            "agent_id": {
                "type": "string",
                "description": (
                    "Optional: only exchanges from this agent's conversations "
                    "(speda, sentinel, atomix, …). Leave unset to search the whole roster."
                ),
            },
            "context_window": {
                "type": "integer",
                "description": "Turns to include either side of each hit (default 2, max 4).",
                "default": 2,
            },
            "limit": {
                "type": "integer",
                "description": "Max matching exchanges to return (default 8, max 20).",
                "default": 8,
            },
        },
        "required": ["query"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        return await search_conversations(context, args)

    async def _search(self, args: dict, context: AgentContext) -> str:
        query = (args.get("query") or "").strip()
        if not query:
            return "No query provided — give a topic or question to recall."
        limit = min(int(args.get("limit", 8) or 8), 20)
        window = min(max(int(args.get("context_window", 2) or 2), 0), 4)
        after = _parse_date(args.get("after"))
        before = _parse_date(args.get("before"))
        agent_filter = (args.get("agent_id") or "").strip() or None

        # Exclude the active session — its messages are already in the model's
        # context and would dominate the ranking (they echo the query's wording).
        # `embedding` is deferred: the vectors come from the in-process cache
        # above, and loading 126 MB of BLOBs here to rebuild what is already in
        # memory was the six seconds this path used to cost.
        stmt = (
            select(Message.id, Message.session_id, Message.created_at, Message.role,
                   Session.title, Session.agent_id, MessageEmbedding.id)
            .join(Session, Message.session_id == Session.id)
            .outerjoin(MessageEmbedding, MessageEmbedding.message_id == Message.id)
            .where(
                Session.user_id == context.user_id,
                Message.session_id != context.session_id,
                Message.role.in_(("user", "assistant")),
            )
        )
        if args.get("_owner_sessions_only"):
            stmt = stmt.where(Session.triggered_by == "user")
        if after:
            stmt = stmt.where(Message.created_at >= after)
        if before:
            stmt = stmt.where(Message.created_at < before + timedelta(days=1))
        if agent_filter:
            stmt = stmt.where(Session.agent_id == agent_filter)
        stmt = stmt.order_by(Message.created_at.desc(), Message.id.desc()).limit(MAX_CANDIDATES)

        lexical_only = args.get("_lexical_only", False)
        lexical_ids = None
        if lexical_only:
            lexical_ids = await lexical.search(
                context.db, query=query, limit=limit * 4, index=lexical.MESSAGES,
                allowed_ids_query=stmt.with_only_columns(Message.id),
            )
            if not lexical_ids:
                return f"No relevant past exchanges found for '{query}'."
            # The local fallback needs metadata only for its matches. Loading
            # every message before FTS would consume a short recall deadline.
            stmt = stmt.where(Message.id.in_(lexical_ids))
        raw_rows = (await context.db.execute(stmt)).all()
        rows = [(SimpleNamespace(id=eid, message_id=mid, session_id=sid,
                    agent_id=agent, created_at=date, role=role), title)
                for mid, sid, date, role, title, agent, eid in raw_rows]
        if not rows:
            scope = self._describe_scope(after, before, agent_filter)
            if scope:
                return (
                    f"No indexed conversation history {scope}. Widen the date range or "
                    f"drop the agent filter."
                )
            return (
                "No stored conversation history in the requested scope."
            )

        # ── The two retrievers ────────────────────────────────────────────────
        # Words first, and locally: this is the half that actually finds a course
        # code, a person's name or a Turkish word the embedding has smoothed
        # away, and it needs no network, so it still ranks when the query
        # embedding fails. Saved messages remain eligible without vectors;
        # only selected windows load full transcript content.
        # Same cross-lingual step the fact tier applies, and for the same
        # reason: a Turkish question cannot reach English text through either
        # retriever unaided. The transcript is mixed-language rather than
        # English-only, which is precisely why the ORIGINAL query is kept for
        # the keyword pass — half the corpus is the language it was typed in.
        from app.services import query_translation

        prepared = args.get("_prepared_query")
        vector_query, lexical_query = (query, query) if lexical_only else (
            prepared[:2] if prepared is not None else await query_translation.expand(query))

        position = {row.message_id: i for i, (row, _) in enumerate(rows)}
        if lexical_ids is None:
            lexical_ids = await lexical.search(
                db=context.db,
                query=lexical_query,
                limit=limit * 4,
                allowed_ids=set(position),
                index=lexical.MESSAGES,
            )
        lexical_ranking = [position[m] for m in lexical_ids if m in position]

        # The relevance floor, for the same reason it exists on the fact tier
        # (app/services/observations.py): RRF ranks but does not judge, so
        # without a floor the closest of 20,000 messages is returned as a match
        # even when nothing in the corpus is about the query at all. Message
        # text is longer and noisier than a distilled fact, so its cosines sit
        # lower for equivalent relevance and it gets its own, looser setting.
        from app.config import settings

        floor = float(settings.recall_message_min_similarity)
        vector_ranking: list[int] = []
        scores = np.zeros(len(rows), dtype=np.float32)
        try:
            if lexical_only:
                raise _LexicalOnly()
            query_vec = prepared[2] if prepared is not None else (await embed_texts([vector_query]))[0]
            recall_cache = context.extra.get("memory_recall_cache")
            if recall_cache is not None and args.get("_owner_sessions_only"):
                pending = warm_conversation_vectors(context, recall_cache)
                # A cold corpus can finish loading after a short recall deadline.
                # Its task owns a separate reader and no chat transaction.
                index, matrix = await asyncio.shield(pending)
            else:
                index, matrix = await _vectors_for(context.db, context.user_id,
                    getattr(recall_cache, "conversation_vectors", None))
            if matrix.size and matrix.shape[1] != len(query_vec):
                context.extra.setdefault("recall_errors", []).append("incompatible_vector_dimensions")
                raise ValueError("Stored conversation vectors do not match the query dimensions")
            # Score the whole corpus in one BLAS call, then read off the rows
            # that survived SQL filtering. A row the cache has not seen yet
            # keeps its zero and is ranked by the lexical half alone.
            corpus = matrix @ query_vec if matrix.size else np.zeros(0, dtype=np.float32)
            for i, (row, _) in enumerate(rows):
                pos = index.get(int(row.id)) if row.id is not None else None
                if pos is not None:
                    scores[i] = corpus[pos]
            vector_ranking = [
                int(i) for i in np.argsort(-scores)[: limit * 4]
                if float(scores[i]) >= floor
            ]
        except _LexicalOnly:
            pass
        except Exception as e:  # noqa: BLE001
            logger.warning("recall_embed_query_failed", extra={"error": str(e)})
            errors = context.extra.setdefault("recall_errors", [])
            if isinstance(e, ValueError):
                if "incompatible_vector_dimensions" not in errors:
                    errors.append("incompatible_vector_dimensions")
            else:
                errors.append("conversation_vector_search_failed:" + type(e).__name__)
            if not lexical_ranking:
                return "Recall is unavailable right now (the embedding call failed)."

        fused = lexical.rrf_fuse(vector_ranking, lexical_ranking, limit=limit * 4)
        if not fused:
            return f"No relevant past exchanges found for '{query}'."
        # The rendered score is the fused agreement between the two rankings,
        # not a cosine — so it has to replace the raw dot products, or the
        # snippet headers would report a number nothing was ordered by.
        for idx, score in fused:
            scores[idx] = score

        # Rank with a per-session diversity cap, so one long conversation can't
        # crowd out every other relevant exchange.
        picked: list[int] = []
        per_session: dict[int, int] = {}
        for idx, _ in fused:
            sid = rows[idx][0].session_id
            if per_session.get(sid, 0) >= MAX_PER_SESSION:
                continue
            per_session[sid] = per_session.get(sid, 0) + 1
            picked.append(int(idx))
            if len(picked) >= limit:
                break

        context.extra.setdefault("recall_trace", []).append({
            "stage": "conversations", "query": query,
            "lexical_only": lexical_only, "candidates": len(rows),
            "selected_message_ids": [rows[i][0].message_id for i in picked],
            "vector_hits": len(vector_ranking), "lexical_hits": len(lexical_ranking),
            "ranked_candidates": [{"message_id": rows[i][0].message_id,
                "session_id": rows[i][0].session_id, "score": score,
                "vector_hit": i in vector_ranking, "lexical_hit": i in lexical_ranking}
                for i, score in fused],
        })
        if not picked:
            return ""
        return await self._render(context, rows, scores, picked, window, query,
                                  args.get("_visible_texts", set()), args.get("_existing_text", ""),
                                  args.get("_receipt_chars", 2800))

    @staticmethod
    def _describe_scope(
        after: datetime | None, before: datetime | None, agent_filter: str | None
    ) -> str:
        bits = []
        if after:
            bits.append(f"after {after.date()}")
        if before:
            bits.append(f"before {before.date()}")
        if agent_filter:
            bits.append(f"from {agent_filter}")
        return " ".join(bits)

    async def _render(
        self,
        context: AgentContext,
        rows,
        scores,
        picked: list[int],
        window: int,
        query: str,
        visible_texts=None,
        existing_text="",
        receipt_chars=2800,
    ) -> str:
        """Expand each hit into a snippet with its surrounding turns, merging
        hits that land close together in the same session."""
        db = context.db

        # Group the chosen hits by session, keeping the best score per session
        # so ordering still reflects relevance rather than recency.
        by_session: dict[int, dict] = {}
        for idx in picked:
            emb, title = rows[idx]
            entry = by_session.setdefault(
                emb.session_id,
                {
                    "title": title or "Untitled conversation",
                    "agent_id": emb.agent_id,
                    "date": emb.created_at,
                    "best": float(scores[idx]),
                    "message_ids": [],
                },
            )
            entry["best"] = max(entry["best"], float(scores[idx]))
            entry["message_ids"].append(emb.message_id)

        out: list[str] = []
        rendered = 0
        for session_id, entry in sorted(
            by_session.items(), key=lambda kv: -kv[1]["best"]
        ):
            order = await _session_message_order(db, session_id)
            position = {mid: i for i, mid in enumerate(order)}
            hits = sorted(
                {position[m] for m in entry["message_ids"] if m in position}
            )
            if not hits:
                continue

            spans = _merge_windows(hits, window)
            wanted: list[int] = []
            for start, end in spans:
                wanted.extend(order[start : end + 1])
            if not wanted:
                continue

            messages = (
                await db.execute(
                    select(Message)
                    .where(Message.id.in_(wanted))
                    .order_by(Message.id.asc())
                )
            ).scalars().all()
            by_id = {m.id: m for m in messages}
            hit_ids = set(entry["message_ids"])

            session_header = (
                f"\n## {entry['title']}  ·  session {session_id}  ·  "
                f"{entry['agent_id']}  ·  {entry['date'].strftime('%Y-%m-%d')}  "
                f"(score {entry['best']:.2f})"
            )
            for span_no, (start, end) in enumerate(spans):
                excerpt = []
                for mid in order[start : end + 1]:
                    message = by_id.get(mid)
                    if message is None:
                        continue
                    text = _extract_text(message.content).replace("\n", " ").strip()
                    if not text:
                        continue
                    receipts = ""
                    if message.role == "assistant":
                        from app.services.chat_history import execution_receipts
                        receipts = execution_receipts(message.content, budget=receipt_chars)
                    duplicate = text in (visible_texts or set()) or (existing_text and text in existing_text)
                    if duplicate and not receipts:
                        continue
                    snippet = "[reply text already in context]" if duplicate else text[:SNIPPET_CHARS] + ("…" if len(text) > SNIPPET_CHARS else "")
                    # Mark the matched turn so the model can tell the hit from
                    # the context that was pulled in around it.
                    marker = "→" if mid in hit_ids else " "
                    excerpt.append(f" {marker} [{message.role} message:{message.id} {message.created_at.isoformat()}] {snippet}")
                    if receipts:
                        excerpt.append(receipts)
                if excerpt:
                    out.append(session_header + "\n" + "\n".join(excerpt))
            rendered += len(hits)

        if not out:
            return f"No relevant past exchanges found for '{query}'."

        logger.info(
            "recall_conversations",
            extra={
                "request_id": context.request_id,
                "query": query,
                "results": rendered,
                "sessions": len(by_session),
            },
        )
        header = (
            f"Found {rendered} relevant exchange(s) across {len(by_session)} "
            f"conversation(s). '→' marks the matched turn; the lines around it are "
            f"context."
        )
        context.extra["selected_conversation_windows"] = out
        return header + "\n" + "\n".join(out)


class _LexicalOnly(Exception):
    """Internal control flow: no provider request on the local fallback pass."""


async def search_conversations(context: AgentContext, args: dict) -> str:
    """Shared read-only retrieval; both automatic recall and the tool use it."""
    return await SemanticSearchSkill()._search(args, context)
