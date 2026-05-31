"""
Conversation history search.

Lets SPEDA search the owner's entire conversation history — not just the current
session's rolling window — filtered by keyword and/or date range. This is recall
over raw past exchanges, complementing the curated memory files.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.context import AgentContext
from app.models.message import Message
from app.models.session import Session
from app.skills.base import Skill

logger = logging.getLogger(__name__)

CANDIDATE_WINDOW = 2500   # max messages scanned per query (perf guard)


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


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


class SearchHistorySkill(Skill):
    name = "search_history"
    description = (
        "Search the owner's past conversations by keyword and/or date range. "
        "Use this to recall what was discussed before, find a previous decision, or "
        "check whether a topic has come up — beyond the current session's visible history. "
        "Returns matching exchanges grouped by conversation, newest first."
    )
    read_only = True
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Keyword or phrase to match in message text. Omit to filter by date only.",
            },
            "after": {
                "type": "string",
                "description": "Only include messages on/after this date (YYYY-MM-DD).",
            },
            "before": {
                "type": "string",
                "description": "Only include messages on/before this date (YYYY-MM-DD).",
            },
            "limit": {
                "type": "integer",
                "description": "Max matching messages to return (default 20, max 50).",
                "default": 20,
            },
        },
        "required": [],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        db = context.db
        user_id = context.user_id

        query = (args.get("query") or "").strip()
        after = _parse_date(args.get("after"))
        before = _parse_date(args.get("before"))
        limit = min(int(args.get("limit", 20) or 20), 50)

        # Build the candidate query: this user's messages, optional date range
        stmt = (
            select(Message, Session.title, Session.id)
            .join(Session, Message.session_id == Session.id)
            .where(Session.user_id == user_id)
        )
        if after:
            stmt = stmt.where(Message.created_at >= after)
        if before:
            stmt = stmt.where(Message.created_at < before + timedelta(days=1))
        stmt = stmt.order_by(Message.created_at.desc()).limit(CANDIDATE_WINDOW)

        result = await db.execute(stmt)
        rows = result.all()

        # Keyword filter (Python-side, on extracted text — content is JSON)
        q_lower = query.lower()
        matches: list[tuple[Message, str | None, int]] = []
        for message, title, sid in rows:
            if message.role not in ("user", "assistant"):
                continue
            text = _extract_text(message.content)
            if not text:
                continue
            if query and q_lower not in text.lower():
                continue
            matches.append((message, title, sid))
            if len(matches) >= limit:
                break

        if not matches:
            scope = []
            if query:
                scope.append(f'matching "{query}"')
            if after:
                scope.append(f"after {after.date()}")
            if before:
                scope.append(f"before {before.date()}")
            scope_str = " ".join(scope) if scope else "in history"
            return f"No conversations found {scope_str}."

        # Group by session, format newest-first
        out: list[str] = [f"Found {len(matches)} matching message(s):\n"]
        last_sid = None
        for message, title, sid in matches:
            if sid != last_sid:
                date = message.created_at.strftime("%Y-%m-%d")
                header = title or "Untitled conversation"
                out.append(f"\n## {header}  ·  session {sid}  ·  {date}")
                last_sid = sid
            text = _extract_text(message.content).replace("\n", " ").strip()
            snippet = text[:280] + ("…" if len(text) > 280 else "")
            out.append(f"  [{message.role}] {snippet}")

        logger.info(
            "search_history",
            extra={
                "request_id": context.request_id,
                "query": query,
                "results": len(matches),
            },
        )
        return "\n".join(out)
