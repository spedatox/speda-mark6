# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

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

Everything above is the automatic, threshold-gated path. `/compact` (owner-typed
in the composer or a Telegram chat) is the manual counterpart: it runs the same
folding logic immediately, ignoring the threshold, and hands back a result the
caller turns into a visible confirmation instead of a silent background log
line — the two entry points share `_run_compaction` so they can't drift.
"""

import logging
from dataclasses import dataclass

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.message import Message
from app.models.session import Session

logger = logging.getLogger(__name__)

KEEP_RECENT_MIN = 6        # always keep at least this many raw messages
SUMMARY_MAX_TOKENS = 1024  # cap the summary so it can never grow unbounded

# The owner-facing "compact this conversation now" command. Mirrors BG_COMMAND
# (app/core/dispatch.py): defined once, imported by every surface that accepts
# it (web chat router, Telegram gateway) so it means the same thing everywhere.
COMPACT_COMMAND = "/compact"


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


@dataclass
class _CompactionResult:
    folded_messages: int
    boundary_id: int
    tokens_before: int
    tokens_after: int


async def _run_compaction(db, session: Session, request_id: str, model: str, *, force: bool) -> _CompactionResult | None:
    """The actual fold: summarize everything old, keep the recent window
    verbatim. `force=False` (the background path) bails out under the token
    threshold; `force=True` (`/compact`) always attempts it. Returns None when
    there's nothing old enough to fold (short conversation) or the model
    returned nothing usable — never raises for those cases, only for a real
    failure (LLM call, DB), which callers are expected to catch."""
    from app.config import settings

    threshold = settings.compaction_threshold_tokens
    keep_tokens = settings.compaction_keep_tokens

    through = session.summary_through_id or 0
    rows = (
        await db.execute(
            select(Message)
            .where(Message.session_id == session.id, Message.id > through)
            .order_by(Message.created_at.asc())
        )
    ).scalars().all()

    # Live context size = prior summary + everything not yet summarized.
    summary_tokens = len(session.summary or "") // 4
    live_tokens = summary_tokens + sum(est_tokens(m.content) for m in rows)
    if not force and live_tokens < threshold:
        return None

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
        return None  # everything is "recent" — nothing old enough to compact

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
        return None

    session.summary = new_summary
    session.summary_through_id = boundary_id
    await db.commit()

    summary_tokens_after = len(new_summary) // 4
    logger.info(
        "session_compacted",
        extra={
            "request_id": request_id,
            "session_id": session.id,
            "folded_messages": len(to_fold),
            "boundary_id": boundary_id,
            "live_tokens_before": live_tokens,
            "summary_tokens_after": summary_tokens_after,
            "forced": force,
        },
    )
    return _CompactionResult(
        folded_messages=len(to_fold),
        boundary_id=boundary_id,
        tokens_before=live_tokens,
        tokens_after=summary_tokens_after + acc,
    )


async def maybe_compact_session(session_id: int, request_id: str, model: str) -> None:
    """Compact a session if its live history exceeds the token threshold.
    Background task — opens its own DB session, never reuses the request one."""
    from app.config import settings

    if not settings.compaction_enabled:
        return

    try:
        async with AsyncSessionLocal() as db:
            session = (
                await db.execute(select(Session).where(Session.id == session_id))
            ).scalar_one_or_none()
            if session is None:
                return
            await _run_compaction(db, session, request_id, model, force=False)
    except Exception as e:  # noqa: BLE001
        logger.error(
            "compaction_error",
            extra={"request_id": request_id, "session_id": session_id, "error": str(e)},
        )


@dataclass
class CompactionOutcome:
    """What `compact_now()` hands back for the owner-typed `/compact` command —
    a structured result, not a status recovered by matching prose (mirrors
    `SpawnOutcome` in app/core/dispatch.py). `message` carries the reply text
    for a failure/no-op; on success the numeric fields drive `compact_ack`."""
    ok: bool
    message: str = ""
    folded_messages: int = 0
    tokens_before: int = 0
    tokens_after: int = 0


async def compact_now(session_id: int, request_id: str, model: str) -> CompactionOutcome:
    """Manual, on-demand compaction for the owner-typed `/compact` command.
    Runs synchronously (unlike `maybe_compact_session`, which is fire-and-forget)
    because the caller needs the outcome to build the reply. Ignores the token
    threshold — the owner asked directly, so it always attempts the fold."""
    from app.config import settings

    if not settings.compaction_enabled:
        return CompactionOutcome(
            ok=False,
            message="Compaction is turned off (compaction_enabled=false).",
        )

    try:
        async with AsyncSessionLocal() as db:
            session = (
                await db.execute(select(Session).where(Session.id == session_id))
            ).scalar_one_or_none()
            if session is None:
                return CompactionOutcome(ok=False, message="No session to compact.")
            result = await _run_compaction(db, session, request_id, model, force=True)
    except Exception as e:  # noqa: BLE001
        logger.error(
            "compact_now_error",
            extra={"request_id": request_id, "session_id": session_id, "error": str(e)},
        )
        return CompactionOutcome(ok=False, message=f"Compaction failed: {e}")

    if result is None:
        return CompactionOutcome(
            ok=False,
            message="Nothing to compact — this conversation is already short enough "
                    "that folding older turns wouldn't free up any context.",
        )

    return CompactionOutcome(
        ok=True,
        folded_messages=result.folded_messages,
        tokens_before=result.tokens_before,
        tokens_after=result.tokens_after,
    )


def compact_ack(outcome: CompactionOutcome) -> str:
    """The owner-facing reply to a `/compact` command, shared by every surface
    that accepts one so they cannot drift (mirrors `bg_ack`)."""
    if not outcome.ok:
        return outcome.message
    saved = max(outcome.tokens_before - outcome.tokens_after, 0)
    plural = "s" if outcome.folded_messages != 1 else ""
    return (
        f"✅ Compacted {outcome.folded_messages} older message{plural} into the "
        f"rolling summary. Context: ~{outcome.tokens_before} → ~{outcome.tokens_after} "
        f"tokens (~{saved} saved). The full history is still visible above — only "
        f"what the model reads next turn was trimmed."
    )
