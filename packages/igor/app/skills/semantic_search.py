"""
Semantic (meaning-based) search over the owner's entire conversation history.

Complements the keyword-only `search_history` skill: that one finds exact
phrases, this one finds relevant past exchanges even when the wording differs
entirely from how the query is phrased. Backed by MessageEmbedding rows
(app/models/message_embedding.py), populated incrementally every turn and
backfilled via POST /admin/index-embeddings (app/services/embedding_indexer.py).
Search is a brute-force cosine similarity over L2-normalized vectors — see
app/services/embeddings.py for why that's the right call at single-user scale.
"""

import logging

import numpy as np
from sqlalchemy import select

from app.core.context import AgentContext
from app.models.message_embedding import MessageEmbedding
from app.models.session import Session
from app.services.embeddings import embed_texts
from app.skills.base import Skill

logger = logging.getLogger(__name__)

MAX_CANDIDATES = 50_000  # perf guard on the brute-force scan; single-user scale


class SemanticSearchSkill(Skill):
    name = "recall_conversations"
    description = (
        "Search the owner's ENTIRE conversation history — across every agent, not just "
        "the current one — by MEANING rather than exact wording. Use this for conceptual "
        "or fuzzy recall: 'have we discussed X before', 'what did I decide about Y', or "
        "synthesizing something the owner mentioned that was never distilled into a "
        "/memories/*.md file. Do NOT use this for an exact phrase, keyword, or date-range "
        "lookup — use search_history for that, since it is faster and more precise for "
        "literal matches. Returns a ranked list of the most relevant past exchanges, each "
        "with its session, originating agent, date, and a text snippet."
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

        try:
            query_vec = (await embed_texts([query]))[0]
        except Exception as e:
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
            .order_by(MessageEmbedding.created_at.desc())
            .limit(MAX_CANDIDATES)
        )
        rows = (await context.db.execute(stmt)).all()
        if not rows:
            return (
                "No indexed conversation history yet. Run POST /admin/index-embeddings "
                "once to backfill past conversations."
            )

        matrix = np.stack([
            np.frombuffer(row.embedding, dtype=np.float32) for row, _ in rows
        ])
        scores = matrix @ query_vec
        # Diversify: cap hits per session so one conversation can't fill the
        # whole result list and crowd out other relevant exchanges.
        max_per_session = 3
        top_idx: list[int] = []
        per_session: dict[int, int] = {}
        for idx in np.argsort(-scores):
            sid = rows[idx][0].session_id
            if per_session.get(sid, 0) >= max_per_session:
                continue
            per_session[sid] = per_session.get(sid, 0) + 1
            top_idx.append(int(idx))
            if len(top_idx) >= limit:
                break

        out = [f"Found {len(top_idx)} relevant past exchange(s):\n"]
        for rank, idx in enumerate(top_idx, start=1):
            row, title = rows[idx]
            date = row.created_at.strftime("%Y-%m-%d")
            header = title or "Untitled conversation"
            out.append(
                f"\n{rank}. [{header} · session {row.session_id} · {row.agent_id} · {date}] "
                f"(score {scores[idx]:.2f})"
            )
            out.append(f"   ({row.role}) {row.text}")

        logger.info(
            "recall_conversations",
            extra={"request_id": context.request_id, "query": query, "results": len(top_idx)},
        )
        return "\n".join(out)
