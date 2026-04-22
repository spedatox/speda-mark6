import logging
import uuid

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import AgentContext
from app.database import get_db
from app.schemas.chat import ChatRequest

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])


@router.post("/chat")
async def chat(
    request: Request,
    body: ChatRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    User-facing chat endpoint.
    Streams SSE events back to Flutter.
    Zero business logic — delegates entirely to orchestrator.run(context).
    """
    orchestrator = request.app.state.orchestrator
    session_manager = request.app.state.session_manager
    profile = request.app.state.profile

    request_id = str(uuid.uuid4())
    user_id = 1  # Single-user system

    session = await session_manager.get_or_create(
        db=db,
        user_id=user_id,
        triggered_by="user",
        model_used=profile.allocate_model("user"),
        session_id=body.session_id,
    )

    history = await session_manager.load_history(db, session.id)
    # Persist and append the user message
    await session_manager.save_message(db, session.id, "user", body.message)
    history.append({"role": "user", "content": body.message})

    context = AgentContext(
        user_id=user_id,
        session_id=session.id,
        request_id=request_id,
        triggered_by="user",
        trigger_payload={"message": body.message},
        output_mode="respond",
        model=profile.allocate_model("user"),
        system_prompt="",  # Set by orchestrator.build_system_prompt()
        conversation_history=history,
        db=db,
        timezone="UTC",  # TODO: load from user record
    )

    collected_chunks: list[str] = []

    async def generate():
        async for event in orchestrator.run(context):
            if event.type.value == "chunk":
                collected_chunks.append(str(event.data))
            yield event.to_sse()

        # Save assistant message after stream completes
        full_response = "".join(collected_chunks)
        if full_response:
            await session_manager.save_message(db, session.id, "assistant", full_response)

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.websocket("/ws")
async def websocket_chat(websocket: WebSocket):
    """
    WebSocket endpoint for Flutter real-time chat (bidirectional, low-latency).
    Accepts the connection and echoes back for now.
    TODO: Implement full WebSocket chat loop with orchestrator.
    """
    await websocket.accept()
    logger.info("ws_flutter_connect")
    try:
        while True:
            data = await websocket.receive_text()
            await websocket.send_text(f"received: {data}")
    except WebSocketDisconnect:
        logger.info("ws_flutter_disconnect")
