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

@router.get("/models")
async def list_models():
    """Models across all configured providers — the LLM layer owns the catalog."""
    from app.services.llm_client import available_models

    return await available_models()


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

    def extract_meta(content) -> dict:
        """Pull the SPEDA display-only meta block (tools + files) so the tool
        disclosure and download cards survive a reload."""
        if not isinstance(content, list):
            return {}
        for block in content:
            if isinstance(block, dict) and block.get('type') == '_speda_meta':
                return {'tools': block.get('tools', []), 'files': block.get('files', [])}
        return {}

    out = []
    for m in messages:
        if m.role not in ('user', 'assistant'):
            continue
        meta = extract_meta(m.content)
        row = {
            'id': str(m.id),
            'role': m.role,
            'content': extract_text(m.content),
            'tools': meta.get('tools', []),
            'isStreaming': False,
            'isError': False,
        }
        if (imgs := extract_images(m.content)):
            row['images'] = imgs
        if meta.get('files'):
            row['files'] = meta['files']
        out.append(row)
    return out


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

    # Save FIRST, then load history including the new message. load_history
    # stamps user messages from their DB created_at, so the prompt built this
    # turn is byte-identical to the prefix reconstructed on every future turn —
    # that identity is what keeps the conversation prompt-cache valid (see
    # SessionManager.stamp_user_content).
    await session_manager.save_message(db, session.id, "user", user_content)
    history = await session_manager.load_history(db, session.id)

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
    collected_tools: list[dict] = []   # {id, name, input, result?}
    collected_files: list[dict] = []

    async def generate():
        async for event in orchestrator.run(context):
            et = event.type.value
            if et == "chunk":
                collected_chunks.append(str(event.data))
            elif et == "tool":
                d = event.data if isinstance(event.data, dict) else {}
                collected_tools.append({"id": d.get("id"), "name": d.get("name"), "input": d.get("input")})
            elif et == "tool_result":
                d = event.data if isinstance(event.data, dict) else {}
                for t in collected_tools:
                    if t.get("id") == d.get("id"):
                        t["result"] = d.get("result")
                        break
            elif et == "file":
                collected_files.append(event.data)
            yield event.to_sse()

        full_response = "".join(collected_chunks)
        if full_response or collected_files:
            # Persist the answer text plus a display-only meta block so the tool
            # disclosure, sources and file cards survive a page reload. The meta
            # block is stripped before history is sent back to Claude.
            content: list = [{"type": "text", "text": full_response}]
            if collected_tools or collected_files:
                content.append({
                    "type": "_speda_meta",
                    "tools": collected_tools,
                    "files": collected_files,
                })
            await session_manager.save_message(db, session.id, "assistant", content)

    schedule_background_tasks(
        background_tasks,
        session_id=session.id,
        request_id=request_id,
        user_id=user_id,
        # Background work (title, session log, maintenance) runs on the SAME
        # provider as the active chat model — never silently burns Anthropic
        # credit while chatting on OpenAI/Gemini, and keeps working in the
        # Dead Zone where only the local Ollama model exists.
        model=profile.background_model(model),
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

                # Save first; load_history returns the new message already
                # timestamp-stamped from its DB created_at (cache-stable).
                await session_manager.save_message(db, session.id, "user", message)
                history = await session_manager.load_history(db, session.id)

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
                _run_background(
                    session.id, request_id, user_id,
                    profile.background_model(profile.allocate_model("user")),
                )
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
