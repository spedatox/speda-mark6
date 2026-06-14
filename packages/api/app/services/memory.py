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
import re
from datetime import datetime, timezone, timedelta

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.memory_file import MemoryFile
from app.models.message import Message
from app.models.session import Session

logger = logging.getLogger(__name__)

LOG_PATH = "/memories/log.md"
CURRENT_PATH = "/memories/current.md"
DOSSIER_PATH = "/memories/dossier.md"
LOG_MAX_ENTRIES = 30   # keep the rolling log bounded

_CURRENT_PROMPT = """\
Today is {date}.

Below is the previous "current" snapshot, the recent session log, and the active
projects file. Produce an UPDATED snapshot of what is genuinely current in the
owner's life as of today — ongoing work, active concerns, near-term plans.

Rules:
- Move anything finished, resolved, or stale OUT. Do not carry it forward.
- Never present an old or completed item as if it were new.
- 3-7 short bullet points. Date-stamp time-sensitive items, e.g. "(as of {date})".
- Return ONLY the bullet list. No header, no preamble.

PREVIOUS SNAPSHOT:
{previous}

RECENT SESSION LOG:
{log}

ACTIVE PROJECTS:
{projects}"""

_DOSSIER_PROMPT = """\
You maintain a private behavioural dossier on the owner — a working model of how
he likes to be treated, inferred from how he REACTS, not from facts he states.

Below is the existing dossier and recent raw exchanges. Update the dossier based on
observable signals: corrections, pushback, frustration, praise, repeated patterns,
what he engages with vs. ignores.

Rules:
- Only record well-supported inferences. Do not invent or over-read single messages.
- Keep each section tight — a few sharp bullets, not paragraphs.
- Carry forward still-valid prior observations; drop ones that no longer hold.
- Return the dossier body with exactly these four sections and nothing else:
  ## Appreciates
  ## Friction / dislikes
  ## Working style
  ## Open questions

EXISTING DOSSIER:
{dossier}

RECENT EXCHANGES:
{exchanges}"""

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
    from app.services.llm_client import LLMClient
    from app.skills.memory import ensure_seeded

    try:
        async with AsyncSessionLocal() as db:
            user_msg, assistant_msg = await _load_last_exchange(db, session_id)
            if not user_msg or not assistant_msg:
                return

            client = LLMClient()
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


# ── Daily maintenance: current brief + behavioural dossier ───────────────────

async def _get_file(db: AsyncSession, user_id: int, path: str) -> "MemoryFile | None":
    result = await db.execute(
        select(MemoryFile).where(
            MemoryFile.user_id == user_id,
            MemoryFile.path == path,
        )
    )
    return result.scalar_one_or_none()


async def _recent_exchanges(db: AsyncSession, user_id: int, days: int = 2, cap: int = 50) -> str:
    """Plain-text dump of the user's recent user/assistant messages for analysis."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    result = await db.execute(
        select(Message)
        .join(Session, Message.session_id == Session.id)
        .where(Session.user_id == user_id, Message.created_at >= since)
        .order_by(Message.created_at.desc())
        .limit(cap)
    )
    rows = list(reversed(result.scalars().all()))
    lines: list[str] = []
    for m in rows:
        if m.role not in ("user", "assistant"):
            continue
        content = m.content
        if isinstance(content, list):
            text = " ".join(
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
        else:
            text = str(content)
        text = text.strip().replace("\n", " ")
        if text:
            lines.append(f"[{m.role}] {text[:600]}")
    return "\n".join(lines)


async def run_daily_maintenance(
    session_id: int,
    request_id: str,
    user_id: int,
    model: str,
) -> None:
    """
    Once per day: refresh /memories/current.md (recency snapshot) and
    /memories/dossier.md (inferred behavioural model). Self-guards on the
    "Last updated" date stamp in current.md so it runs at most once per day.
    """
    from app.services.llm_client import LLMClient
    from app.skills.memory import ensure_seeded

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        async with AsyncSessionLocal() as db:
            await ensure_seeded(user_id, db)

            current = await _get_file(db, user_id, CURRENT_PATH)
            # Guard: already refreshed today?
            if current and f"_Last updated: {today}_" in current.content:
                return

            log = await _get_file(db, user_id, LOG_PATH)
            projects = await _get_file(db, user_id, "/memories/projects.md")
            dossier = await _get_file(db, user_id, DOSSIER_PATH)
            exchanges = await _recent_exchanges(db, user_id)

            client = LLMClient()

            # ── Refresh current.md ────────────────────────────────────────────
            try:
                resp = await client.create_message(
                    model=model,
                    system="You maintain a concise recency snapshot. Follow instructions exactly.",
                    messages=[{
                        "role": "user",
                        "content": _CURRENT_PROMPT.format(
                            date=today,
                            previous=(current.content if current else "")[:2000],
                            log=(log.content if log else "")[:2000],
                            projects=(projects.content if projects else "")[:1500],
                        ),
                    }],
                    max_tokens=512,
                )
                bullets = (resp.content[0].text.strip() if resp.content else "")
                if bullets:
                    new_current = (
                        "# Current — what's active right now\n\n"
                        f"_Last updated: {today}_\n\n"
                        f"{bullets}\n"
                    )
                    if current:
                        current.content = new_current
                        current.updated_at = datetime.now(timezone.utc)
                    else:
                        db.add(MemoryFile(user_id=user_id, path=CURRENT_PATH, content=new_current))
                    await db.commit()
                    logger.info("current_brief_refreshed", extra={"request_id": request_id})
            except Exception as e:
                logger.error("current_brief_error", extra={"request_id": request_id, "error": str(e)})

            # ── Update dossier.md ─────────────────────────────────────────────
            if exchanges:
                try:
                    resp = await client.create_message(
                        model=model,
                        system="You maintain a precise behavioural dossier. Follow instructions exactly.",
                        messages=[{
                            "role": "user",
                            "content": _DOSSIER_PROMPT.format(
                                dossier=(dossier.content if dossier else "")[:3000],
                                exchanges=exchanges[:6000],
                            ),
                        }],
                        max_tokens=1024,
                    )
                    body = (resp.content[0].text.strip() if resp.content else "")
                    if body and "##" in body:
                        new_dossier = (
                            "# Dossier — behavioural analysis\n\n"
                            "_A private, inferred model of the owner — how he likes to be "
                            "treated, read from how he reacts. Shared working knowledge; "
                            "tailor behaviour to it silently._\n\n"
                            f"_Last updated: {today}_\n\n"
                            f"{body}\n"
                        )
                        if dossier:
                            dossier.content = new_dossier
                            dossier.updated_at = datetime.now(timezone.utc)
                        else:
                            db.add(MemoryFile(user_id=user_id, path=DOSSIER_PATH, content=new_dossier))
                        await db.commit()
                        logger.info("dossier_updated", extra={"request_id": request_id})
                except Exception as e:
                    logger.error("dossier_error", extra={"request_id": request_id, "error": str(e)})

    except Exception as e:
        logger.error("daily_maintenance_error", extra={"request_id": request_id, "error": str(e)})


# ── Title generation ──────────────────────────────────────────────────────────

def _clean_title(raw: str) -> str:
    """Normalise a model's title output into a single clean line. Handles the
    ways different providers wrap output: code fences (Ollama coder models),
    'Title:' prefixes, surrounding quotes, trailing punctuation, multi-line."""
    if not raw:
        return ""
    text = raw.strip().strip("`").strip()
    # first non-empty line only
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    line = re.sub(r"^(?:conversation\s+)?title\s*[:\-]\s*", "", line, flags=re.I)
    line = re.sub(r"\s+", " ", line).strip()
    # strip wrapping quotes AND trailing/leading punctuation, repeatedly (handles
    # e.g. '"Black Hole Basics".' → 'Black Hole Basics')
    line = line.strip("\"'“”‘’.,;:!?-—– ").strip()
    return line[:80]


def _fallback_title(seed: str) -> str:
    """Deterministic title derived from the user's message — never fails, so a
    session is ALWAYS titled even if every model call returns empty or errors."""
    text = (seed or "").strip()
    # defensively strip a leading per-message timestamp stamp, if present
    text = re.sub(r"^\[\d{4}-\d{2}-\d{2}[^\]]*\]\s*", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return "New conversation"
    short = " ".join(text.split(" ")[:8]).rstrip(".,!?;:").strip()
    if len(short) > 60:
        short = short[:60].rstrip() + "…"
    return (short[:1].upper() + short[1:]) if short else "New conversation"


async def generate_title(
    session_id: int,
    request_id: str,
    model: str,
) -> None:
    """
    Background task: title a session after the first exchange. Idempotent (only
    runs when the session has no title yet).

    A title is GUARANTEED regardless of provider: we start from a deterministic
    fallback derived from the user's message, then best-effort upgrade it with
    the model. If the model returns empty (reasoning models on a tight budget)
    or the provider is down (no key, dead zone, rate limit), the fallback stands
    — the session never gets stuck on "New conversation".
    """
    from app.services.llm_client import LLMClient

    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Session).where(Session.id == session_id))
            session = result.scalar_one_or_none()

            if session is None or session.title is not None:
                return

            user_msg, assistant_msg = await _load_last_exchange(db, session_id)
            if not user_msg and not assistant_msg:
                return  # truly nothing to title

            # 1) Deterministic fallback — this alone guarantees a title.
            title = _fallback_title(user_msg or assistant_msg)
            source = "fallback"

            # 2) Best-effort upgrade to a model-written title (provider-agnostic).
            try:
                client = LLMClient()
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
                    # Generous cap + minimal reasoning so this works uniformly across
                    # providers: reasoning models (GPT-5, Gemini 2.5) otherwise burn
                    # the whole budget thinking and emit no title. Anthropic/Ollama
                    # ignore reasoning_effort and stop early anyway, so the cap is
                    # just a ceiling there.
                    max_tokens=512,
                    reasoning_effort="minimal",
                )
                cleaned = _clean_title(response.content[0].text if response.content else "")
                if cleaned:
                    title, source = cleaned, "model"
            except Exception as e:
                logger.warning(
                    "title_model_failed_using_fallback",
                    extra={"request_id": request_id, "model": model, "error": str(e)},
                )

            session.title = title[:255]
            await db.commit()
            logger.info(
                "title_generated",
                extra={"request_id": request_id, "session_id": session_id, "title": title, "source": source},
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
    Schedule post-turn background work:
      - session log update (every turn)
      - daily maintenance: current brief + dossier (self-guards to once/day)
      - title generation (first turn only — idempotent)
    All open their own DB sessions (never reuse the request session).
    """
    background_tasks.add_task(update_session_log, session_id, request_id, user_id, model)
    background_tasks.add_task(run_daily_maintenance, session_id, request_id, user_id, model)
    background_tasks.add_task(generate_title, session_id, request_id, model)