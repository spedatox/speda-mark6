"""Mailbox watch probes. Both owner API key and n8n secret are required."""
import asyncio
from fastapi import APIRouter,Request
from app.schemas.mail import MailScanRequest,MailSeenRequest
from app.services.n8n import validate_n8n_secret

router=APIRouter(tags=['mail'])


async def scan_provider(request,body,provider):
    validate_n8n_secret(request)
    try: return await request.app.state.mail_watches.scan(1,provider,body)
    except TimeoutError: return {'status':'time_limit','count':0,'message_ids':[],'messages':[],'partial':True,'detail':'Scan deadline reached; no batch was acknowledged.'}


@router.post('/mail/watch/scan')
async def scan(request:Request,body:MailScanRequest): return await scan_provider(request,body,'google')


@router.post('/outlook/watch/scan')
async def outlook_scan(request:Request,body:MailScanRequest): return await scan_provider(request,body,'microsoft')


@router.post('/mail/watch/seen')
async def seen(request:Request,body:MailSeenRequest):
    validate_n8n_secret(request)
    return await request.app.state.mail_watches.acknowledge(1,'google',body)


@router.post('/outlook/watch/seen')
async def outlook_seen(request:Request,body:MailSeenRequest):
    validate_n8n_secret(request)
    return await request.app.state.mail_watches.acknowledge(1,'microsoft',body)
