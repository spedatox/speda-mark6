"""
Rebuilding the memory record from raw history (v3 §6).

This is the capability v2 could not have. When files hold knowledge that exists
nowhere else, re-indexing destroys it, so the extraction can only ever be
patched. Once files are derived output, a rebuild costs nothing but compute:
improve the prompt, re-run, get a better record — with everything the owner
authored untouched.

The pipeline, and why each step is where it is:

  1. PRESERVE   rows with origin in (owner, seed, live) are never regenerated.
                Owner writes are ground truth (§4.3). Seed rows came from files
                that no longer exist to re-read. Live rows are judgements an
                agent made WITH the owner in front of it, which a transcript
                does not reliably reproduce.
  2. SEED       (first run only) the pre-v3 markdown files are parsed into
                observations. This is what makes the first rebuild
                non-destructive; it never runs again.
  3. DERIVE     history is walked in batches, one cheap structured call each —
                Honcho's "minimal deriver" shape (a single call per batch, not
                an agentic loop) because predictable cost matters more than
                flexibility when the same job runs over months of transcript.
  4. RENDER     the six derived surfaces are regenerated from the new record.

Nothing here schedules itself. It runs when an admin endpoint asks (§CLAUDE.md:
n8n owns when), and it holds no DB session across an LLM call.
"""

import asyncio
import json
import logging
import re
from datetime import date, datetime, timezone

from sqlalchemy import delete as sql_delete, func, select

from app.database import AsyncSessionLocal
from app.models.memory_file import MemoryFile
from app.models.message import Message
from app.models.observation import Observation
from app.models.session import Session
from app.services.observations import DOMAINS, record_observations

logger = logging.getLogger(__name__)

# Characters of transcript per extraction call. Sized so a batch is a coherent
# stretch of conversation rather than a fragment — a fact stated in one turn and
# qualified in the next must land in the same batch or the qualifier is lost.
BATCH_CHARS = 12_000

# Extraction calls in flight. The provider rate limiter governs the real rate;
# this only stops a long history from opening hundreds of sockets at once.
CONCURRENCY = 6

# Hard ceiling on one extraction call. Without this a single unanswered request
# holds its concurrency slot forever and the whole rebuild stalls behind it —
# which is exactly what happened on the first real run: four hours, zero rows,
# no error. A batch that times out is skipped, not retried into the same hang.
BATCH_TIMEOUT_SECONDS = 120

# Batches extracted before results are written. The first implementation
# gathered EVERY batch and wrote once at the end, so a large history showed no
# progress at all until it finished — and a crash at 95% lost everything.
# Writing in chunks makes progress visible in the record itself and makes the
# job resumable in practice: whatever landed stays landed.
WRITE_CHUNK = 10

# Per-batch ceiling on what one stretch of conversation may claim. A batch that
# yields thirty "facts" has started transcribing rather than distilling.
MAX_PER_BATCH = 12

_EXTRACT_SYSTEM = (
    "You extract durable facts about a person from conversation transcripts. "
    "You are precise, conservative, and you never invent. Return only JSON."
)

_EXTRACT_PROMPT = """\
Below is a stretch of conversation between the OWNER and an AI assistant, dated \
{period}. Extract only DURABLE facts about the owner or the people and projects \
in his life — things that would still matter months from now.

Return a JSON array. Each element:

{{
  "content":    "one self-contained sentence, under 300 characters",
  "subject":    "owner" | "person:<Name>" | "project:<Name>",
  "domain":     one of: {domains},
  "valid_from": "YYYY-MM-DD" or null,
  "valid_until":"YYYY-MM-DD" or null
}}

Rules — these are hard:
- ONE fact per element. If a sentence needs an "and", it is two facts.
- Absolute dates only. Resolve "last month", "in the spring" against the \
conversation's own date ({period}); if you cannot resolve it, use null.
- `valid_until` is for something that had ALREADY STOPPED being true at the time \
of this conversation. Something merely stated in the past stays null.
- `domain` guide: biography = who someone is (never expires, so never give it a \
valid_until). preference = what he likes, dislikes or wants and in what manner. \
state = true of his life at the time. project = a project's status. training = \
a gym session. finance = a figure, account or budget. event = a dated thing \
that happened, usually to someone else.
- Skip: pleasantries, task chatter, questions he asked, what the assistant said \
about itself, anything about the assistant, credentials, one-off moods.
- If the stretch contains nothing durable, return [].
- At most {max_items} elements. Prefer fewer, better facts.

TRANSCRIPT:
{transcript}

Return only the JSON array."""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def _parse_json_array(raw: str) -> list[dict]:
    """Pull a JSON array out of a model response.

    Providers wrap output differently — fences, a preamble, a trailing note — and
    an extraction job that discards a whole batch over a code fence is an
    extraction job that quietly loses months of history. Tolerant on the way in,
    strict on what it accepts as an element.
    """
    text = (raw or "").strip()
    if not text:
        return []
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    return [p for p in parsed if isinstance(p, dict) and p.get("content")]


async def _load_batches(user_id: int) -> list[tuple[str, str]]:
    """Whole history as (period_label, transcript) batches, oldest first.

    Batching follows session boundaries first and character budget second, so a
    batch is a coherent stretch of one conversation rather than an arbitrary
    window spanning two unrelated ones.
    """
    batches: list[tuple[str, str]] = []
    async with AsyncSessionLocal() as db:
        session_rows = (
            await db.execute(
                select(Session.id, Session.started_at)
                .where(Session.user_id == user_id)
                .order_by(Session.started_at.asc())
            )
        ).all()

        for session_id, started_at in session_rows:
            messages = (
                await db.execute(
                    select(Message)
                    .where(
                        Message.session_id == session_id,
                        Message.role.in_(("user", "assistant")),
                    )
                    .order_by(Message.id.asc())
                )
            ).scalars().all()

            label = started_at.strftime("%Y-%m-%d") if started_at else "unknown date"
            current: list[str] = []
            size = 0
            for m in messages:
                text = _extract_text(m.content).strip()
                if not text:
                    continue
                line = f"{m.role.upper()}: {text}"
                if size + len(line) > BATCH_CHARS and current:
                    batches.append((label, "\n\n".join(current)))
                    current, size = [], 0
                current.append(line)
                size += len(line)
            if current:
                batches.append((label, "\n\n".join(current)))
    return batches


# ── Step 2: seed from the pre-v3 files ────────────────────────────────────────

# `- [2026-07-06, sentinel] wants totals before breakdowns.` and plainer bullets.
_BULLET = re.compile(r"^\s*[-*]\s+(.*\S)\s*$")
_STAMP = re.compile(r"^\[(\d{4}-\d{2}-\d{2})(?:,\s*([a-z0-9_+ ]+))?\]\s*(.*)$", re.I)

# Which domain a pre-v3 file's contents become. The old taxonomy maps onto the
# new one cleanly because the new one was derived from it.
_FILE_DOMAIN: dict[str, tuple[str, str]] = {
    # path: (subject, domain)
    "/memories/owner.md": ("owner", "biography"),
    "/memories/current.md": ("owner", "state"),
    "/memories/dossier.md": ("owner", "preference"),
    "/memories/projects.md": ("", "project"),      # subject from the ## heading
    "/memories/social.md": ("", "event"),          # subject from the ## heading
    "/memories/sessions.md": ("owner", "training"),
    "/memories/finance.md": ("owner", "finance"),
    "/memories/history.md": ("owner", "state"),    # ended — valid_until set below
}


def _parse_file(path: str, content: str) -> list[dict]:
    """Turn one pre-v3 markdown file into observation proposals.

    Best-effort by nature: prose that was never structured cannot be recovered
    perfectly. The mitigation is not cleverness here but `compare_to_stored` —
    run the shadow report before flipping and see exactly what did not survive.
    """
    default_subject, domain = _FILE_DOMAIN.get(path, ("owner", "state"))
    is_history = path == "/memories/history.md"
    proposals: list[dict] = []
    heading: str | None = None

    for line in content.splitlines():
        if line.startswith("## "):
            heading = line[3:].strip()
            continue
        match = _BULLET.match(line)
        if not match:
            continue
        body = match.group(1).strip()
        # Drop the rendered-file annotations and template comments.
        body = re.sub(r"\s*<sub>.*?</sub>\s*$", "", body).strip()
        if not body or body.startswith("_") or body.startswith("<!--"):
            continue

        stamped = _STAMP.match(body)
        observer, when = "owner", None
        if stamped:
            when = stamped.group(1)
            observer = (stamped.group(2) or "owner").split("+")[0].strip() or "owner"
            body = stamped.group(3).strip()
        if not body:
            continue

        subject = default_subject
        if not subject and heading:
            kind = "project" if path.endswith("projects.md") else "person"
            subject = f"{kind}:{heading}"
        subject = subject or "owner"

        proposal = {
            "content": body[:600],
            "level": "explicit",
            "subject": subject,
            "domain": domain,
            "valid_from": when,
        }
        if is_history:
            # It is in history.md, so by construction it stopped being true. We
            # do not know when; the file's own date is the best evidence, and
            # today is the honest fallback. Never leave it null — that would
            # resurrect an ended fact as current, which is the one direction
            # this migration must not fail in.
            proposal["valid_until"] = when or date.today().isoformat()
            proposal["valid_from"] = None
        proposals.append({k: v for k, v in proposal.items() if v is not None or k in ("valid_from", "valid_until")})
    return proposals


_SEED_SYSTEM = (
    "You convert a personal-memory note file into discrete facts. You are "
    "exhaustive about what the file states and you never add anything. Return "
    "only JSON."
)

_SEED_PROMPT = """\
Below is one file from a personal AI assistant's memory about its owner. Convert \
EVERY distinct fact it states into a separate entry. This file is being migrated \
into a structured record and anything you leave out is lost, so err towards \
including a fact rather than skipping it.

Return a JSON array. Each element:

{{
  "content":    "one self-contained sentence, under 300 characters",
  "subject":    "owner" | "person:<Name>" | "project:<Name>",
  "domain":     one of: {domains},
  "valid_from": "YYYY-MM-DD" or null,
  "valid_until":"YYYY-MM-DD" or null
}}

Rules:
- Cover PROSE as well as bullet points. A paragraph of biography is several \
facts; split it into them.
- Under a `## Heading` naming a person or project, that heading is the subject.
- ONE fact per element. Keep the file's own wording where you can.
- Absolute dates only, taken from the file. Never invent one; use null.
- {validity}
- Default subject for this file: `{subject}`. Default domain: `{domain}`.
- Skip: section headers, template comments, instructions to agents, "(none yet)" \
placeholders, and any line explaining how the file itself works.

FILE {path}:
{content}

Return only the JSON array."""


async def _extract_file_prose(model: str, path: str, content: str) -> list[dict]:
    """Model pass over one memory file — the half regex cannot do.

    The bullet parser recovers structured entries exactly, including their
    `[date, agent]` stamps. It cannot recover prose, and the pre-v3 files are
    full of it: owner.md's whole biography, every `**Who:**` block in social.md.
    Regex alone would migrate the shape of the memory and drop its substance.
    """
    from app.services.llm_client import LLMClient

    subject, domain = _FILE_DOMAIN.get(path, ("owner", "state"))
    validity = (
        "Everything in this file has ALREADY ENDED — give every entry a "
        "`valid_until` (the file's own date if it has one, otherwise null and we "
        "will fill it in)."
        if path.endswith("history.md")
        else "Leave `valid_until` null unless the file explicitly says the thing "
        "stopped."
    )
    try:
        resp = await LLMClient().create_message(
            model=model,
            system=_SEED_SYSTEM,
            messages=[{
                "role": "user",
                "content": _SEED_PROMPT.format(
                    domains=", ".join(DOMAINS),
                    path=path,
                    subject=subject or "(from the ## heading)",
                    domain=domain,
                    validity=validity,
                    content=content[:20_000],
                ),
            }],
            max_tokens=4096,
        )
        raw = resp.content[0].text if resp.content else ""
    except Exception as e:  # noqa: BLE001
        logger.error("memory_seed_prose_failed", extra={"path": path, "error": str(e)})
        return []

    proposals = _parse_json_array(raw)
    for p in proposals:
        p["level"] = "explicit"
        p.pop("source_ids", None)
        if path.endswith("history.md") and not p.get("valid_until"):
            # A fact from history.md with no end date would resurrect as current.
            p["valid_until"] = date.today().isoformat()
    return proposals


async def seed_from_files(
    user_id: int, request_id: str = "", model: str | None = None
) -> dict:
    """
    One-time: convert the existing markdown files into observations.

    Two passes per file, because neither alone is sufficient:

      - the **bullet parser** recovers structured entries exactly, keeping their
        `[YYYY-MM-DD, agent]` attribution, which a model would paraphrase away;
      - the **model pass** recovers everything else — prose biography, `**Who:**`
        blocks, narrative paragraphs — which the parser silently drops.

    Overlap between the two is harmless: identical content collapses into
    reinforcement on write. Omission is not harmless, which is why both run.
    Passing `model=None` skips the prose pass; do that only if you have already
    confirmed the files hold no prose.

    Skipped entirely if any seed row already exists — running it twice would
    duplicate hand-curated knowledge, and it has no way to tell its own previous
    output from a genuine repeat.
    """
    async with AsyncSessionLocal() as db:
        already = (
            await db.execute(
                select(func.count())
                .select_from(Observation)
                .where(Observation.user_id == user_id, Observation.origin == "seed")
            )
        ).scalar()
        if already:
            logger.info("memory_seed_skipped", extra={"existing": already})
            return {"skipped": True, "existing": already}

        files = (
            await db.execute(
                select(MemoryFile).where(MemoryFile.user_id == user_id)
            )
        ).scalars().all()
        by_path = {f.path: f.content for f in files}

    stored_total, rejected_total = 0, 0
    per_file: dict[str, dict] = {}
    for path, content in sorted(by_path.items()):
        if path not in _FILE_DOMAIN or not content.strip():
            continue

        bullets = _parse_file(path, content)
        prose = await _extract_file_prose(model, path, content) if model else []
        # Bullets first: on a content collision the structured entry wins the
        # insert and the model's paraphrase reinforces it rather than replacing
        # it, so attribution and dates survive.
        proposals = bullets + prose
        if not proposals:
            continue

        async with AsyncSessionLocal() as db:
            stored, rejections = await record_observations(
                db,
                user_id=user_id,
                observer="owner",
                proposals=proposals,
                request_id=request_id,
                origin="seed",
            )
        stored_total += len(stored)
        rejected_total += len(rejections)
        per_file[path] = {
            "from_bullets": len(bullets),
            "from_prose": len(prose),
            "stored": len(stored),
            "rejected": len(rejections),
        }
        logger.info("memory_seed_file", extra={"path": path, **per_file[path]})

    return {
        "skipped": False,
        "stored": stored_total,
        "rejected": rejected_total,
        "files": per_file,
        "prose_pass": bool(model),
    }


# ── Step 3: derive from history ───────────────────────────────────────────────

async def _record_progress(
    request_id: str, done: int, total: int, stored: int
) -> None:
    """Write "batch N of M" onto the queue row driving this rebuild.

    Matched by `request_id`, which is unique per job, so no handler signature has
    to carry a job id around. Best-effort: a failure to report progress must
    never stop the work it is reporting on.
    """
    if not request_id:
        return
    try:
        from app.models.background_job import BackgroundJob

        async with AsyncSessionLocal() as db:
            job = (
                await db.execute(
                    select(BackgroundJob).where(
                        BackgroundJob.request_id == request_id,
                        BackgroundJob.kind == "memory_reindex",
                    )
                )
            ).scalars().first()
            if job is None:
                return
            job.payload = {
                **(job.payload or {}),
                "progress": {"done": done, "total": total, "stored": stored},
            }
            await db.commit()
    except Exception as e:  # noqa: BLE001
        logger.debug("reindex_progress_write_failed", extra={"error": str(e)})


async def _extract_batch(model: str, period: str, transcript: str) -> list[dict]:
    """One extraction call. Returns proposals; never raises."""
    from app.services.llm_client import LLMClient

    try:
        resp = await asyncio.wait_for(
            LLMClient().create_message(
                model=model,
                system=_EXTRACT_SYSTEM,
                messages=[{
                    "role": "user",
                    "content": _EXTRACT_PROMPT.format(
                        period=period,
                        domains=", ".join(DOMAINS),
                        max_items=MAX_PER_BATCH,
                        transcript=transcript,
                    ),
                }],
                max_tokens=2048,
            ),
            timeout=BATCH_TIMEOUT_SECONDS,
        )
        raw = resp.content[0].text if resp.content else ""
    except asyncio.TimeoutError:
        logger.warning(
            "memory_extract_timeout",
            extra={"period": period, "seconds": BATCH_TIMEOUT_SECONDS},
        )
        return []
    except Exception as e:  # noqa: BLE001
        logger.warning("memory_extract_failed", extra={"period": period, "error": str(e)})
        return []

    proposals = _parse_json_array(raw)[:MAX_PER_BATCH]
    for p in proposals:
        p["level"] = "explicit"        # derivation cannot cite sources it never saw
        p.pop("source_ids", None)
        p.pop("supersedes", None)
    return proposals


async def derive_from_history(user_id: int, model: str, request_id: str = "") -> dict:
    """
    Walk the whole history and rebuild the derived half of the record.

    Deletes prior `origin="reindex"` rows FIRST, hard, so improving the prompt
    and re-running does not accumulate near-duplicates of everything. That hard
    delete is the one place the no-destruction doctrine does not apply, and it is
    safe precisely because these rows are reproducible output — the transcripts
    they came from are untouched.
    """
    async with AsyncSessionLocal() as db:
        removed = (
            await db.execute(
                sql_delete(Observation).where(
                    Observation.user_id == user_id,
                    Observation.origin == "reindex",
                )
            )
        ).rowcount or 0
        await db.commit()
    if removed:
        logger.info("memory_reindex_cleared", extra={"removed": removed})

    batches = await _load_batches(user_id)
    if not batches:
        return {"batches": 0, "stored": 0, "rejected": 0, "cleared": removed}

    logger.info(
        "memory_reindex_start",
        extra={"request_id": request_id, "batches": len(batches), "model": model},
    )

    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def _one(period: str, transcript: str) -> list[dict]:
        async with semaphore:
            return await _extract_batch(model, period, transcript)

    # Extract in chunks and write after each one. Extraction still holds no DB
    # session (Honcho's rule) — the session opens only between chunks — but the
    # record now grows as the job runs instead of appearing all at once at the
    # end, so "is this working?" is answerable by looking at it.
    stored_total, rejected_total, timed_out = 0, 0, 0
    for start in range(0, len(batches), WRITE_CHUNK):
        chunk = batches[start : start + WRITE_CHUNK]
        results = await asyncio.gather(
            *(_one(period, text) for period, text in chunk), return_exceptions=True
        )
        for proposals in results:
            if isinstance(proposals, BaseException):
                timed_out += 1
                continue
            if not proposals:
                continue
            async with AsyncSessionLocal() as db:
                stored, rejections = await record_observations(
                    db,
                    user_id=user_id,
                    observer="orion",
                    proposals=proposals,
                    request_id=request_id,
                    origin="reindex",
                )
            stored_total += len(stored)
            rejected_total += len(rejections)

        done = min(start + WRITE_CHUNK, len(batches))
        await _record_progress(request_id, done, len(batches), stored_total)
        logger.info(
            "memory_reindex_progress",
            extra={
                "request_id": request_id,
                "batch": done,
                "of": len(batches),
                "stored": stored_total,
            },
        )

    logger.info(
        "memory_reindex_derived",
        extra={
            "request_id": request_id,
            "batches": len(batches),
            "stored": stored_total,
            "rejected": rejected_total,
            "failed_batches": timed_out,
        },
    )
    return {
        "batches": len(batches),
        "stored": stored_total,
        "rejected": rejected_total,
        "cleared": removed,
    }


# ── The pipeline ──────────────────────────────────────────────────────────────

async def reindex(
    user_id: int,
    model: str,
    *,
    request_id: str = "",
    seed: bool = True,
    render: bool = True,
) -> dict:
    """
    The full rebuild (v3 §6). Safe to re-run: only reproducible rows are replaced.

    `seed=False` skips the one-time file parse — use it for every rebuild after
    the first, though the seed step self-guards anyway.
    """
    started = datetime.now(timezone.utc)
    report: dict = {"request_id": request_id}

    if seed:
        report["seed"] = await seed_from_files(user_id, request_id, model=model)
    report["derive"] = await derive_from_history(user_id, model, request_id)

    if render:
        from app.services.memory_render import commit_rendered

        async with AsyncSessionLocal() as db:
            report["rendered"] = await commit_rendered(
                db, user_id, request_id=request_id, author="reindex"
            )

    async with AsyncSessionLocal() as db:
        report["record"] = {
            origin: count
            for origin, count in (
                await db.execute(
                    select(Observation.origin, func.count())
                    .where(
                        Observation.user_id == user_id,
                        Observation.deleted_at.is_(None),
                    )
                    .group_by(Observation.origin)
                )
            ).all()
        }

    report["seconds"] = round((datetime.now(timezone.utc) - started).total_seconds(), 1)
    logger.info("memory_reindex_complete", extra=report)
    return report
