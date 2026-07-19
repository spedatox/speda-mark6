"""
Conversation compaction — caps per-turn input cost on long chats.

On a long conversation the API re-receives the entire growing transcript every
turn (even with prompt caching, each new turn's cache write covers the full
history). This is the single biggest cost driver. Compaction summarizes the
OLD turns into a compact rolling summary and keeps only the recent window
verbatim, so the model sees [summary] + [last ~N turns] instead of everything.

Design (CLAUDE.md Rule 7 — never block the SSE stream):
  - Runs as a BackgroundTask AFTER a turn completes, never inline.
  - Raw messages are NEVER deleted — the UI still shows the full history. Only
    SessionManager.load_history (the model's context) is compacted, gated on
    session.summary / summary_through_id.
  - Rolling: a re-compaction folds the prior summary + the newly-aged turns into
    an updated summary, so the summary stays bounded as the chat grows.
  - Summarization runs on the cheap background model (Haiku).
"""

import logging

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.message import Message
from app.models.session import Session

logger = logging.getLogger(__name__)

KEEP_RECENT_MIN = 6        # always keep at least this many raw messages
SUMMARY_MAX_TOKENS = 1024  # cap the summary so it can never grow unbounded


def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if not isinstance(b, dict):
                continue
            t = b.get("type")
            if t == "text":
                parts.append(b.get("text", ""))
            elif t == "tool_use":
                parts.append(f"[called tool: {b.get('name', '?')}]")
            elif t == "tool_result":
                parts.append("[tool result]")
            elif t == "image":
                parts.append("[image]")
        return " ".join(p for p in parts if p)
    return str(content)


def est_tokens(content) -> int:
    """Rough token estimate (chars/4) for a message's content. Images carry a
    fixed vision-token estimate since their base64 isn't billed as text."""
    if isinstance(content, list):
        total = 0
        for b in content:
            if isinstance(b, dict) and b.get("type") == "image":
                total += 1200
            else:
                total += len(_extract_text([b])) // 4 if isinstance(b, dict) else 0
        return total
    return len(_extract_text(content)) // 4


_SUMMARY_PROMPT = """\
You are compacting the EARLIER part of an ongoing conversation so it can be
dropped from context without losing anything the assistant needs to continue
seamlessly. The recent turns are kept verbatim and are NOT shown to you.

Write a dense, factual summary that preserves:
- What the user asked for and the current task / goal state
- Decisions made, conclusions reached, and any specific facts, names, numbers,
  URLs, file names or code identifiers that may be referenced later
- Anything still open or in progress
Do NOT add commentary, do NOT include pleasantries, do NOT invent. If a prior
summary is given, MERGE the new material into it and return ONE updated summary.

PRIOR SUMMARY:
{prior}

EARLIER TURNS TO FOLD IN:
{transcript}

Return only the updated summary."""


async def maybe_compact_session(session_id: int, request_id: str, model: str) -> None:
    """Compact a session if its live history exceeds the token threshold.
    Background task — opens its own DB session, never reuses the request one."""
    from app.config import settings

    if not settings.compaction_enabled:
        return

    threshold = settings.compaction_threshold_tokens
    keep_tokens = settings.compaction_keep_tokens

    try:
        async with AsyncSessionLocal() as db:
            session = (
                await db.execute(select(Session).where(Session.id == session_id))
            ).scalar_one_or_none()
            if session is None:
                return

            through = session.summary_through_id or 0
            rows = (
                await db.execute(
                    select(Message)
                    .where(Message.session_id == session_id, Message.id > through)
                    .order_by(Message.created_at.asc())
                )
            ).scalars().all()

            # Live context size = prior summary + everything not yet summarized.
            summary_tokens = len(session.summary or "") // 4
            live_tokens = summary_tokens + sum(est_tokens(m.content) for m in rows)
            if live_tokens < threshold:
                return

            # Keep the most recent messages (by token budget, min KEEP_RECENT_MIN).
            kept_ids: set[int] = set()
            acc = 0
            for m in reversed(rows):
                kept_ids.add(m.id)
                acc += est_tokens(m.content)
                if len(kept_ids) >= KEEP_RECENT_MIN and acc >= keep_tokens:
                    break

            to_fold = [m for m in rows if m.id not in kept_ids]
            if not to_fold:
                return  # everything is "recent" — nothing old enough to compact

            boundary_id = max(m.id for m in to_fold)
            transcript = "\n\n".join(
                f"{m.role.upper()}: {_extract_text(m.content)}".strip()
                for m in to_fold
                if _extract_text(m.content).strip()
            )

            from app.services.llm_client import LLMClient

            client = LLMClient()
            resp = await client.create_message(
                model=model,
                system="You compact conversation context precisely. Follow instructions exactly.",
                messages=[{
                    "role": "user",
                    "content": _SUMMARY_PROMPT.format(
                        prior=session.summary or "(none)",
                        transcript=transcript[:30000],
                    ),
                }],
                max_tokens=SUMMARY_MAX_TOKENS,
            )
            new_summary = (resp.content[0].text.strip() if resp.content else "")
            if not new_summary:
                return

            session.summary = new_summary
            session.summary_through_id = boundary_id
            await db.commit()
            logger.info(
                "session_compacted",
                extra={
                    "request_id": request_id,
                    "session_id": session_id,
                    "folded_messages": len(to_fold),
                    "boundary_id": boundary_id,
                    "live_tokens_before": live_tokens,
                    "summary_tokens_after": len(new_summary) // 4,
                },
            )
    except Exception as e:  # noqa: BLE001
        logger.error(
            "compaction_error",
            extra={"request_id": request_id, "session_id": session_id, "error": str(e)},
        )
