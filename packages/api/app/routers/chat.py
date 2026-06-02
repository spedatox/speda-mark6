import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import AgentContext
from app.database import AsyncSessionLocal, get_db
from app.schemas.chat import ChatRequest
from app.services.memory import schedule_background_tasks

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])

AVAILABLE_MODELS = [
    {
        "id": "claude-opus-4-7",
        "name": "Claude Opus 4.7",
        "description": "Most capable — complex reasoning & deep analysis",
        "tags": ["powerful"],
    },
    {
        "id": "claude-sonnet-4-6",
        "name": "Claude Sonnet 4.6",
        "description": "Smart and efficient for most tasks",
        "tags": ["fast", "default"],
    },
    {
        "id": "claude-haiku-4-5-20251001",
        "name": "Claude Haiku 4.5",
        "description": "Fastest — great for simple, quick tasks",
        "tags": ["fastest"],
    },
]


@router.get("/models")
async def list_models():
    return AVAILABLE_MODELS


@router.get("/budget-mode")
async def get_budget_mode_endpoint():
    """Current budget-mode state (for the UI toggle)."""
    from app.core.runtime_state import get_budget_mode
    return {"budget_mode": get_budget_mode()}


@router.post("/budget-mode")
async def set_budget_mode_endpoint(body: dict):
    """Toggle budget mode. Body: {\"enabled\": true|false}. Persists across restarts."""
    from app.core.runtime_state import set_budget_mode
    enabled = bool(body.get("enabled", True))
    return {"budget_mode": set_budget_mode(enabled)}


@router.get("/sessions/{session_id}/messages")
async def get_messages(
    session_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select, asc
    from app.models.message import Message

    result = await db.execute(
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(asc(Message.created_at))
    )
    messages = result.scalars().all()

    def extract_text(content) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return ''.join(
                block.get('text', '') for block in content
                if isinstance(block, dict) and block.get('type') == 'text'
            )
        return ''

    def extract_images(content) -> list[str]:
        """Rebuild data: URLs from stored base64 image blocks so attachments
        re-render when an old session is reopened (they're persisted in the DB)."""
        if not isinstance(content, list):
            return []
        out = []
        for block in content:
            if isinstance(block, dict) and block.get('type') == 'image':
                src = block.get('source', {})
                if src.get('type') == 'base64' and src.get('data'):
                    out.append(f"data:{src.get('media_type', 'image/png')};base64,{src['data']}")
        return out

    return [
        {
            'id': str(m.id),
            'role': m.role,
            'content': extract_text(m.content),
            'tools': [],
            'isStreaming': False,
            'isError': False,
            **({'images': imgs} if (imgs := extract_images(m.content)) else {}),
        }
        for m in messages
        if m.role in ('user', 'assistant')
    ]


@router.get("/sessions")
async def list_sessions(
    request: Request,
    limit: int = 500,
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select, desc
    from app.models.session import Session

    result = await db.execute(
        select(Session)
        .where(Session.user_id == 1)
        .order_by(desc(Session.started_at))
        .limit(limit)
    )
    sessions = result.scalars().all()
    return [
        {"id": s.id, "title": s.title, "started_at": s.started_at.isoformat()}
        for s in sessions
    ]


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: int,
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import delete
    from app.models.session import Session
    from app.models.message import Message

    await db.execute(delete(Message).where(Message.session_id == session_id))
    await db.execute(delete(Session).where(Session.id == session_id))
    await db.commit()
    return {"ok": True}


@router.patch("/sessions/{session_id}")
async def rename_session(
    session_id: int,
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select
    from app.models.session import Session

    result = await db.execute(select(Session).where(Session.id == session_id))
    session = result.scalar_one_or_none()
    if session:
        session.title = body.get("title", session.title)
        await db.commit()
    return {"ok": True}


@router.post("/chat")
async def chat(
    request: Request,
    body: ChatRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    orchestrator = request.app.state.orchestrator
    session_manager = request.app.state.session_manager
    profile = request.app.state.profile

    request_id = str(uuid.uuid4())
    user_id = 1

    model = body.model or profile.allocate_model("user")
    system_prompt = body.system_prompt or ""

    session = await session_manager.get_or_create(
        db=db,
        user_id=user_id,
        triggered_by="user",
        model_used=model,
        session_id=body.session_id,
    )

    # Build the user turn — plain text, or a content-block array when images are attached.
    if body.attachments:
        user_content: list | str = [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": att.media_type, "data": att.data},
            }
            for att in body.attachments
        ]
        if body.message:
            user_content.append({"type": "text", "text": body.message})
    else:
        user_content = body.message

    history = await session_manager.load_history(db, session.id)
    await session_manager.save_message(db, session.id, "user", user_content)
    history.append({"role": "user", "content": user_content})

    context = AgentContext(
        user_id=user_id,
        session_id=session.id,
        request_id=request_id,
        triggered_by="user",
        trigger_payload={"message": body.message},
        output_mode="respond",
        model=model,
        system_prompt=system_prompt,
        conversation_history=history,
        db=db,
        timezone="UTC",
    )

    collected_chunks: list[str] = []

    async def generate():
        async for event in orchestrator.run(context):
            if event.type.value == "chunk":
                collected_chunks.append(str(event.data))
            yield event.to_sse()

        full_response = "".join(collected_chunks)
        if full_response:
            await session_manager.save_message(db, session.id, "assistant", full_response)

    schedule_background_tasks(
        background_tasks,
        session_id=session.id,
        request_id=request_id,
        user_id=user_id,
        model=profile.haiku_model,
    )

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.websocket("/ws")
async def websocket_chat(websocket: WebSocket):
    await websocket.accept()

    orchestrator = websocket.app.state.orchestrator
    session_manager = websocket.app.state.session_manager
    profile = websocket.app.state.profile

    logger.info("ws_flutter_connect")

    try:
        while True:
            raw = await websocket.receive_text()

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_text(
                    json.dumps({"type": "error", "data": "Invalid JSON"})
                )
                continue

            message = data.get("message", "").strip()
            session_id_req = data.get("session_id")

            if not message:
                continue

            request_id = str(uuid.uuid4())
            user_id = 1

            async with AsyncSessionLocal() as db:
                session = await session_manager.get_or_create(
                    db=db,
                    user_id=user_id,
                    triggered_by="user",
                    model_used=profile.allocate_model("user"),
                    session_id=session_id_req,
                )

                history = await session_manager.load_history(db, session.id)
                await session_manager.save_message(db, session.id, "user", message)
                history.append({"role": "user", "content": message})

                context = AgentContext(
                    user_id=user_id,
                    session_id=session.id,
                    request_id=request_id,
                    triggered_by="user",
                    trigger_payload={"message": message},
                    output_mode="respond",
                    model=profile.allocate_model("user"),
                    system_prompt="",
                    conversation_history=history,
                    db=db,
                    timezone="UTC",
                )

                collected_chunks: list[str] = []

                async for event in orchestrator.run(context):
                    if event.type.value == "chunk":
                        collected_chunks.append(str(event.data))
                    await websocket.send_text(event.to_json())

                full_response = "".join(collected_chunks)
                if full_response:
                    await session_manager.save_message(
                        db, session.id, "assistant", full_response
                    )

            asyncio.create_task(
                _run_background(session.id, request_id, user_id, profile.haiku_model)
            )

    except WebSocketDisconnect:
        logger.info("ws_flutter_disconnect")
    except Exception as e:
        logger.error("ws_error", extra={"error": str(e)})
        try:
            await websocket.send_text(json.dumps({"type": "error", "data": str(e)}))
        except Exception:
            pass


async def _run_background(
    session_id: int, request_id: str, user_id: int, model: str
) -> None:
    from app.services.memory import update_session_log, generate_title

    await asyncio.gather(
        update_session_log(session_id, request_id, user_id, model),
        generate_title(session_id, request_id, model),
        return_exceptions=True,
    )
