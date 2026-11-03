"""Authenticated pairing and secret-authenticated durable Telegram intake."""
import hmac
from fastapi import APIRouter,Request,HTTPException
from app.config import settings
from app.services.n8n import validate_n8n_secret

router=APIRouter(tags=['telegram'])


@router.post('/telegram/webhook/{agent_id}')
async def webhook(agent_id:str,request:Request):
    secret=request.headers.get('X-Telegram-Bot-Api-Secret-Token','')
    if not settings.telegram_webhook_secret or not hmac.compare_digest(secret,settings.telegram_webhook_secret):
        raise HTTPException(403,'Invalid Telegram webhook secret')
    length=request.headers.get('content-length')
    if length and (not length.isdigit() or int(length)>64000): raise HTTPException(413,'Update exceeds 64 KiB')
    raw=await request.body()
    if len(raw)>64000: raise HTTPException(413,'Update exceeds 64 KiB')
    import json
    try: event=json.loads(raw)
    except ValueError: raise HTTPException(422,'Invalid Telegram update')
    if not isinstance(event,dict): raise HTTPException(422,'Update must be an object')
    return await request.app.state.telegram_gateway.receive(1,agent_id,event)


@router.get('/telegram/link/{agent_id}')
async def link(agent_id:str,request:Request):
    return await request.app.state.telegram_gateway.link(1,agent_id)


@router.get('/telegram/status')
async def status(request:Request):
    return await request.app.state.telegram_gateway.status(1)


@router.delete('/telegram/link/{agent_id}')
async def disconnect(agent_id:str,request:Request):
    return await request.app.state.telegram_gateway.disconnect(1,agent_id)


@router.post('/telegram/pending/drain')
async def drain(request:Request):
    validate_n8n_secret(request)
    return await request.app.state.telegram_gateway.drain(1)


@router.get('/deliveries')
async def deliveries(request:Request,run_id:str|None=None):
    return {'deliveries':await request.app.state.deliveries.list(1,run_id)}


@router.post('/deliveries/pending/drain')
async def deliver(request:Request):
    validate_n8n_secret(request)
    return await request.app.state.deliveries.drain(1)
