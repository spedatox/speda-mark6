from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from app.database import get_db
from app.schemas.chat import ChatRequest
from app.schemas.client_compat import ClientChat

router = APIRouter(tags=["chat"])


@router.websocket("/ws")
async def websocket_chat(websocket: WebSocket):
    if hasattr(websocket.app.state, 'client_compat'):
        return await websocket.app.state.client_compat.websocket(websocket)
    await websocket.app.state.chat_turns.websocket(websocket)


@router.post("/chat")
async def chat(request: Request, body: ClientChat | ChatRequest, db: AsyncSession = Depends(get_db)):
    if hasattr(request.app.state, 'client_compat'):
        from app.routers.client_compat import chat as client_chat
        if isinstance(body, ChatRequest):
            blocks = body.message if isinstance(body.message, list) else [{'type':'text','text':body.message}]
            body = ClientChat(message='\n'.join(b['text'] for b in blocks if b['type'] == 'text'),
                attachments=[{'media_type':b['source']['media_type'], 'data':b['source']['data']}
                    for b in blocks if b['type'] == 'image'], session_id=body.session_id, model=body.model, thinking=body.thinking)
        return await client_chat(request, 'speda', body)
    turns = request.app.state.chat_turns
    try:
        context = await turns.start(db, body)
    except ValueError as exc:
        raise HTTPException(status_code=404 if "owner" in str(exc) else 422, detail=str(exc)) from exc
    return StreamingResponse(turns.stream(context), media_type="text/event-stream",
        background=BackgroundTask(turns.after_turn, context))
