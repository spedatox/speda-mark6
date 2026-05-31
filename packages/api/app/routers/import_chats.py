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
    One-time: mine durable facts about the owner from the entire conversation
    history (Haiku) and write a consolidated profile to /memories/history.md.
    Runs in the background. Pass ?force=true to re-index.
    """
    from app.services.history_indexer import index_history

    profile = request.app.state.profile
    request_id = str(uuid.uuid4())
    background_tasks.add_task(index_history, 1, request_id, profile.haiku_model, force)
    return JSONResponse({"accepted": True, "message": "History indexing started in background"})


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
