"""Voice is an authenticated transport over the app-owned speech service."""
import asyncio
import hmac
from fastapi import APIRouter,Request,UploadFile,File,Form,WebSocket,WebSocketDisconnect
from fastapi.responses import FileResponse
from app.config import settings
from app.schemas.voice import SpeakRequest,VoiceAgentPatch
from app.services.coordination_budget import CoordinationError

router=APIRouter(prefix='/voice',tags=['voice'])


@router.get('/status')
async def status(request:Request): return request.app.state.voice.status()


@router.get('/voices')
async def voices(request:Request): return await request.app.state.voice.voices()


@router.get('/agents')
async def agents(request:Request): return await request.app.state.voice.agents(1)


@router.put('/agents/{agent_id}')
async def edit(agent_id:str,request:Request,body:VoiceAgentPatch):
    await request.app.state.voice.update(1,agent_id,body)
    return await request.app.state.voice.agents(1)


@router.post('/speak')
async def speak(request:Request,body:SpeakRequest):
    result=await request.app.state.voice.speak(1,body)
    return FileResponse(result['path'],media_type=result['content_type'],headers={'Cache-Control':'no-store','X-Voice-Generated':'true'})


@router.post('/listen')
async def listen(request:Request,audio:UploadFile=File(...),locale:str|None=Form(None),request_id:str|None=Form(None)):
    raw=await audio.read(2*1024*1024+1)
    if len(raw)>2*1024*1024: raise CoordinationError('voice_audio','Audio exceeds 2 MiB',422)
    return await request.app.state.voice.listen(1,raw,locale,request_id)


@router.websocket('/stream')
async def stream(websocket:WebSocket):
    await websocket.accept()
    try: hello=await asyncio.wait_for(websocket.receive_json(),timeout=5)
    except (TimeoutError,ValueError,WebSocketDisconnect): await websocket.close(code=1008); return
    if not isinstance(hello,dict) or not isinstance(hello.get('key'),str) or not hmac.compare_digest(hello['key'],settings.speda_api_key):
        await websocket.close(code=1008); return
    await websocket.send_json({'type':'unsupported','detail':'Use the bounded HTTP speech transport.'})
    await websocket.close()
