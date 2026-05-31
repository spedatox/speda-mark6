"""
Background memory services.

Memory architecture follows Anthropic's agent memory pattern: SPEDA owns its
memory and writes structured files via the `memory` tool during conversations
(see app/skills/memory.py). These background tasks are the SUPPLEMENT to that —
specifically the "end-of-session update" Anthropic recommends:

  - update_session_log(): after each turn, append a one-line dated summary to
    /memories/log.md so SPEDA always has a recent-session trail to read back.
  - generate_title(): name the conversation for the sidebar.

The old flat-fact "memories" table (app/models/memory.py) is superseded by the
file-based system and no longer written here.
"""

import logging
from datetime import datetime, timezone

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.memory_file import MemoryFile
from app.models.message import Message
from app.models.session import Session

logger = logging.getLogger(__name__)

LOG_PATH = "/memories/log.md"
LOG_MAX_ENTRIES = 30   # keep the rolling log bounded

_TITLE_PROMPT = """\
Generate a short title (3-6 words) for this conversation.
Return ONLY the title — no punctuation, no quotes, nothing else.

USER: {user_message}
ASSISTANT: {assistant_message}"""

_LOG_PROMPT = """\
Summarise this conversation exchange in ONE short line (max 14 words) for a \
session log — what the owner wanted and what was done. No fluff, no preamble.
Return ONLY the line.

USER: {user_message}
ASSISTANT: {assistant_message}"""


# ── Shared helper ─────────────────────────────────────────────────────────────

async def _load_last_exchange(db: AsyncSession, session_id: int) -> tuple[str, str]:
    """Return (last_user_message, last_assistant_message) for a session."""
    result = await db.execute(
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at.desc())
        .limit(10)
    )
    messages = list(reversed(result.scalars().all()))

    user_msg = assistant_msg = ""
    for m in reversed(messages):
        if not assistant_msg and m.role == "assistant":
            content = m.content
            if isinstance(content, list):
                parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                assistant_msg = " ".join(parts)
            else:
                assistant_msg = str(content)
        elif not user_msg and m.role == "user":
            content = m.content
            if isinstance(content, list):
                parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                user_msg = " ".join(parts)
            else:
                user_msg = str(content)
        if user_msg and assistant_msg:
            break

    return user_msg, assistant_msg


# ── End-of-session log update (Anthropic memory pattern) ──────────────────────

async def update_session_log(
    session_id: int,
    request_id: str,
    user_id: int,
    model: str,
) -> None:
    """
    Append a one-line dated summary of this exchange to /memories/log.md.
    Keeps the log bounded to the most recent LOG_MAX_ENTRIES entries.
    """
    from app.services.anthropic_client import AnthropicClient
    from app.skills.memory import ensure_seeded

    try:
        async with AsyncSessionLocal() as db:
            user_msg, assistant_msg = await _load_last_exchange(db, session_id)
            if not user_msg or not assistant_msg:
                return

            client = AnthropicClient()
            response = await client.create_message(
                model=model,
                system="You write terse one-line session log entries. Follow instructions exactly.",
                messages=[{
                    "role": "user",
                    "content": _LOG_PROMPT.format(
                        user_message=user_msg[:1500],
                        assistant_message=assistant_msg[:1500],
                    ),
                }],
                max_tokens=64,
            )
            summary = (response.content[0].text.strip() if response.content else "")
            summary = summary.strip().splitlines()[0].strip() if summary else ""
            if not summary:
                return

            await ensure_seeded(user_id, db)
            result = await db.execute(
                select(MemoryFile).where(
                    MemoryFile.user_id == user_id,
                    MemoryFile.path == LOG_PATH,
                )
            )
            log_file = result.scalar_one_or_none()
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            entry = f"- {date} — {summary}"

            if log_file is None:
                log_file = MemoryFile(
                    user_id=user_id,
                    path=LOG_PATH,
                    content=f"# Session Log\n\n{entry}\n",
                )
                db.add(log_file)
            else:
                # Insert new entry at the top of the list, keep header, cap length
                lines = log_file.content.splitlines()
                header = lines[0] if lines and lines[0].startswith("#") else "# Session Log"
                entries = [l for l in lines[1:] if l.strip().startswith("- ")]
                entries.insert(0, entry)
                entries = entries[:LOG_MAX_ENTRIES]
                log_file.content = header + "\n\n" + "\n".join(entries) + "\n"
                log_file.updated_at = datetime.now(timezone.utc)

            await db.commit()
            logger.info(
                "session_log_updated",
                extra={"request_id": request_id, "session_id": session_id},
            )

    except Exception as e:
        logger.error(
            "session_log_error",
            extra={"request_id": request_id, "error": str(e)},
        )


# ── Title generation ──────────────────────────────────────────────────────────

async def generate_title(
    session_id: int,
    request_id: str,
    model: str,
) -> None:
    """
    Background task: generate a short session title after the first exchange.
    Only runs if the session has no title yet — idempotent.
    """
    from app.services.anthropic_client import AnthropicClient

    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Session).where(Session.id == session_id))
            session = result.scalar_one_or_none()

            if session is None or session.title is not None:
                return

            user_msg, assistant_msg = await _load_last_exchange(db, session_id)
            if not user_msg or not assistant_msg:
                return

            client = AnthropicClient()
            response = await client.create_message(
                model=model,
                system="You generate short conversation titles. Follow instructions exactly.",
                messages=[{
                    "role": "user",
                    "content": _TITLE_PROMPT.format(
                        user_message=user_msg[:500],
                        assistant_message=assistant_msg[:500],
                    ),
                }],
                max_tokens=32,
            )

            title = response.content[0].text.strip() if response.content else ""
            title = title.strip('"\'').strip()[:255]

            if title:
                session.title = title
                await db.commit()
                logger.info(
                    "title_generated",
                    extra={"request_id": request_id, "session_id": session_id, "title": title},
                )

    except Exception as e:
        logger.error(
            "title_generate_error",
            extra={"request_id": request_id, "error": str(e)},
        )


def schedule_background_tasks(
    background_tasks: BackgroundTasks,
    session_id: int,
    request_id: str,
    user_id: int,
    model: str,
) -> None:
    """
    Schedule post-turn background work: session log update + title generation.
    Both open their own DB sessions (never reuse the request session).
    """
    background_tasks.add_task(update_session_log, session_id, request_id, user_id, model)
    background_tasks.add_task(generate_title, session_id, request_id, model)
