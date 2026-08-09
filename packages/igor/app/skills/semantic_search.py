"""
Semantic (meaning-based) search over the owner's entire conversation history.

Complements the keyword-only `search_history` skill: that one finds exact
phrases, this one finds relevant past exchanges even when the wording differs
entirely from how the query is phrased. Backed by MessageEmbedding rows
(app/models/message_embedding.py), populated incrementally every turn and
backfilled via POST /admin/index-embeddings (app/services/embedding_indexer.py).
Search is a brute-force cosine similarity over L2-normalized vectors — see
app/services/embeddings.py for why that's the right call at single-user scale.

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

import logging
from datetime import datetime, timedelta, timezone

import numpy as np
from sqlalchemy import select

from app.core.context import AgentContext
from app.models.message import Message
from app.models.message_embedding import MessageEmbedding
from app.models.session import Session
from app.services.embeddings import embed_texts
from app.skills.base import Skill

logger = logging.getLogger(__name__)

MAX_CANDIDATES = 50_000   # perf guard on the brute-force scan; single-user scale
MAX_PER_SESSION = 3       # diversity cap so one conversation can't fill the list
SNIPPET_CHARS = 400       # per-message text budget inside a rendered snippet


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
        "/memories file. Do NOT use it for an exact phrase or keyword you already know the "
        "wording of — `search_history` is faster and more precise for literal matches — and "
        "do NOT use it to look up facts the roster has already distilled, which is "
        "`search_memory`. Returns matching exchanges as snippets that include the turns "
        "either side of each hit, grouped by conversation, each tagged with its session, "
        "originating agent and date."
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
        query = (args.get("query") or "").strip()
        if not query:
            return "No query provided — give a topic or question to recall."
        limit = min(int(args.get("limit", 8) or 8), 20)
        window = min(max(int(args.get("context_window", 2) or 2), 0), 4)
        after = _parse_date(args.get("after"))
        before = _parse_date(args.get("before"))
        agent_filter = (args.get("agent_id") or "").strip() or None

        try:
            query_vec = (await embed_texts([query]))[0]
        except Exception as e:  # noqa: BLE001
            logger.warning("recall_embed_query_failed", extra={"error": str(e)})
            return "Semantic recall is unavailable right now (embedding call failed)."

        # Exclude the active session — its messages are already in the model's
        # context and would dominate the ranking (they echo the query's wording).
        stmt = (
            select(MessageEmbedding, Session.title)
            .join(Session, MessageEmbedding.session_id == Session.id)
            .where(
                MessageEmbedding.user_id == context.user_id,
                MessageEmbedding.session_id != context.session_id,
            )
        )
        if after:
            stmt = stmt.where(MessageEmbedding.created_at >= after)
        if before:
            stmt = stmt.where(MessageEmbedding.created_at < before + timedelta(days=1))
        if agent_filter:
            stmt = stmt.where(MessageEmbedding.agent_id == agent_filter)
        stmt = stmt.order_by(MessageEmbedding.created_at.desc()).limit(MAX_CANDIDATES)

        rows = (await context.db.execute(stmt)).all()
        if not rows:
            scope = self._describe_scope(after, before, agent_filter)
            if scope:
                return (
                    f"No indexed conversation history {scope}. Widen the date range or "
                    f"drop the agent filter."
                )
            return (
                "No indexed conversation history yet. Run POST /admin/index-embeddings "
                "once to backfill past conversations."
            )

        matrix = np.stack([
            np.frombuffer(row.embedding, dtype=np.float32) for row, _ in rows
        ])
        scores = matrix @ query_vec

        # Rank with a per-session diversity cap, so one long conversation can't
        # crowd out every other relevant exchange.
        picked: list[int] = []
        per_session: dict[int, int] = {}
        for idx in np.argsort(-scores):
            sid = rows[idx][0].session_id
            if per_session.get(sid, 0) >= MAX_PER_SESSION:
                continue
            per_session[sid] = per_session.get(sid, 0) + 1
            picked.append(int(idx))
            if len(picked) >= limit:
                break

        return await self._render(context, rows, scores, picked, window, query)

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

            out.append(
                f"\n## {entry['title']}  ·  session {session_id}  ·  "
                f"{entry['agent_id']}  ·  {entry['date'].strftime('%Y-%m-%d')}  "
                f"(score {entry['best']:.2f})"
            )
            for span_no, (start, end) in enumerate(spans):
                if span_no:
                    out.append("   …")
                for mid in order[start : end + 1]:
                    message = by_id.get(mid)
                    if message is None:
                        continue
                    text = _extract_text(message.content).replace("\n", " ").strip()
                    if not text:
                        continue
                    snippet = text[:SNIPPET_CHARS] + ("…" if len(text) > SNIPPET_CHARS else "")
                    # Mark the matched turn so the model can tell the hit from
                    # the context that was pulled in around it.
                    marker = "→" if mid in hit_ids else " "
                    out.append(f" {marker} [{message.role}] {snippet}")
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
        return header + "\n" + "\n".join(out)
