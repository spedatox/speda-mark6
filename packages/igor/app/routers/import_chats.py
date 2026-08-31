# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

import io
import json
import logging
import uuid
import zipfile
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.session_manager import SessionManager
from app.database import AsyncSessionLocal
from app.models.message import Message

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/index-history")
async def index_history_endpoint(
    request: Request,
    background_tasks: BackgroundTasks,
    force: bool = False,
):
    """
    Rebuild the memory record from the entire conversation history (v3 §6).

    This is the settings panel's "Index history" button. It used to run
    `services/history_indexer.py`, which mined a prose profile and wrote it
    straight into /memories/history.md. Under v3 that would be actively harmful:
    history.md is a RENDERED file, so the profile would be silently wiped by the
    next render, and history.md is now specifically the file for facts that have
    STOPPED being true — exactly the wrong home for a biography.

    So it now runs the v3 pipeline: seed the pre-v3 files into the record (first
    run only, bullets AND prose), derive the rest from history, re-render the
    surfaces. Safe to press twice — only reproducible rows are replaced, and a
    second press while one is running is a no-op.

    Queued rather than run inline: it is a long job and the durable queue is what
    makes its outcome visible afterwards. Poll GET /admin/memory/status.
    """
    from app.services.task_queue import drain, enqueue_one

    # Owner-level (cross-agent) job — run it on the cheap tier of whatever the
    # default agent is ROUTED to, not a hardcoded profile field: pinning that
    # agent to another provider must move this job with it.
    profile = request.app.state.profiles.default
    model = profile.background_model(profile.allocate_model("user"))
    request_id = str(uuid.uuid4())

    job_id = await enqueue_one(
        kind="memory_reindex", user_id=1, model=model, request_id=request_id
    )
    if job_id is None:
        return JSONResponse({
            "accepted": False,
            "message": "A reindex is already running. Watch its progress in memory status.",
        })

    background_tasks.add_task(drain)
    logger.info("memory_reindex_queued", extra={"request_id": request_id, "job_id": job_id})
    return JSONResponse({
        "accepted": True,
        "job_id": job_id,
        "message": "Rebuilding memory from your whole history. This takes a few minutes.",
    })


@router.post("/index-embeddings")
async def index_embeddings_endpoint(
    background_tasks: BackgroundTasks,
):
    """
    Build BOTH halves of recall over past messages: the semantic vector for any
    message that lacks one, and the keyword index row for any message that lacks
    THAT (see app/skills/semantic_search.py). Recall is hybrid, so a corpus with
    only one of the two indexed is only half-searchable — and since the vector
    half shipped years earlier, that is precisely the state an existing install
    is in. Idempotent and self-healing: safe to call any time, repeatedly; each
    pass only processes what is missing. Runs in the background.
    """
    from app.services.embedding_indexer import backfill_embeddings, backfill_lexical

    request_id = str(uuid.uuid4())

    async def _both() -> None:
        # Lexical first: it is local and finishes in seconds, so recall improves
        # immediately instead of after the embedding backfill's rate-limited
        # crawl through every unembedded message.
        await backfill_lexical(1, request_id)
        await backfill_embeddings(1, request_id)

    background_tasks.add_task(_both)
    return JSONResponse({"accepted": True, "message": "Recall backfill started in background"})


@router.post("/import-chats")
async def import_chats(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    """
    Import chats from a Claude export zip (containing conversations.json).
    Each conversation becomes a session; each chat_message becomes a message.
    Runs in the background — returns immediately.
    """
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="File must be a .zip archive")

    try:
        contents = await file.read()
    except Exception as e:
        logger.error("import_read_failed", extra={"error": str(e)})
        raise HTTPException(status_code=500, detail="Could not read uploaded file")
    finally:
        await file.close()

    background_tasks.add_task(process_import_file, contents)
    return JSONResponse({"accepted": True, "message": "Import started in background"})


async def process_import_file(contents: bytes) -> None:
    """Extract conversations.json from the zip (in memory) and import each conversation."""
    try:
        with zipfile.ZipFile(io.BytesIO(contents)) as zf:
            conv_entry = next(
                (n for n in zf.namelist() if n.endswith("conversations.json")), None
            )
            if conv_entry is None:
                logger.error("import_no_conversations_json")
                return
            with zf.open(conv_entry) as f:
                conversations = json.load(f)
    except Exception as e:
        logger.error("import_unpack_failed", extra={"error": str(e)})
        return

    total = len(conversations)
    logger.info("import_start", extra={"conversations": total})

    imported = 0
    for conv in conversations:
        if await process_conversation(conv):
            imported += 1

    logger.info("import_complete", extra={"imported": imported, "total": total})


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _build_message(session_id: int, msg: dict) -> Message | None:
    """Construct a Message from a Claude export chat_message (no DB write)."""
    try:
        text = msg.get("text", "")
        sender = msg.get("sender", "")
        role = "user" if sender == "human" else "assistant"

        content = msg.get("content")
        if not content:
            content = [{"type": "text", "text": text}] if text else []

        return Message(
            session_id=session_id,
            role=role,
            content=content,
            created_at=_parse_dt(msg.get("created_at")) or datetime.now(timezone.utc),
        )
    except Exception as e:
        logger.warning("import_skip_message", extra={"uuid": msg.get("uuid"), "error": str(e)})
        return None


async def process_conversation(conv: dict) -> bool:
    """Create a session for one conversation and batch-insert all its messages."""
    async with AsyncSessionLocal() as db:
        try:
            session_mgr = SessionManager()
            session = await session_mgr.get_or_create(
                db=db,
                user_id=1,
                triggered_by="user",
                model_used="import",
                session_id=None,  # always create a fresh session
            )

            session.title = conv.get("name", "Imported Conversation")
            started_at = _parse_dt(conv.get("created_at"))
            ended_at = _parse_dt(conv.get("updated_at"))
            if started_at:
                session.started_at = started_at
            if ended_at:
                session.ended_at = ended_at

            # Batch all messages into a single commit
            count = 0
            for msg in conv.get("chat_messages", []):
                message = _build_message(session.id, msg)
                if message is not None:
                    db.add(message)
                    count += 1

            await db.commit()
            logger.info(
                "import_conversation",
                extra={"session_id": session.id, "messages": count, "title": session.title},
            )
            return True

        except Exception as e:
            await db.rollback()
            logger.error(
                "import_conversation_failed",
                extra={"uuid": conv.get("uuid"), "error": str(e)},
            )
            return False
