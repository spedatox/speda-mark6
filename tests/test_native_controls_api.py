import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from app.middleware.auth import APIKeyMiddleware
from app.config import settings
from app.routers.automations import router as automations_router
from app.routers.telegram import router as telegram_router
from app.routers.voice import router as voice_router
from app.routers.reminders import router as reminders_router
from app.routers.agents import router as agents_router
from app.services.reminders import Reminders
from app.services.voice import Voice
from app.services.global_budget import GlobalBudget
from app.services.coordination_budget import CoordinationError
from tests.test_automations import build
from tests.test_delivery import channel


async def test_original_settings_surfaces_and_secret_only_webhook_auth(memory,monkeypatch):
    bits,gateway,deliveries,_=await channel(memory)
    manager=bits.coordinator.automations
    app=FastAPI(); app.state.automations=manager; app.state.telegram_gateway=gateway
    app.state.deliveries=deliveries; app.state.profiles=bits.profiles
    app.state.coordinator=bits.coordinator
    app.state.voice=Voice(memory.sessions,bits.profiles,GlobalBudget(memory.sessions),manager.config)
    app.state.reminders=Reminders(bits.coordinator,deliveries)
    for router in (automations_router,telegram_router,voice_router,reminders_router,agents_router): app.include_router(router)
    @app.exception_handler(CoordinationError)
    async def error(request,exc): return JSONResponse(status_code=exc.status,content={'detail':str(exc),'code':exc.code})
    app.add_middleware(APIKeyMiddleware)
    monkeypatch.setattr(settings,'telegram_webhook_secret','fixture-webhook-secret')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://native.test') as client:
        for url in ('/agents/comms','/agents/asks','/automations','/automations/agents','/automations/status','/voice/status','/voice/agents','/telegram/status','/reminders/open','/reminders/definitions'):
            assert (await client.get(url)).status_code==401
            response=await client.get(url,headers={'X-API-Key':'test-only-key'})
            assert response.status_code==200,(url,response.text)
        assert (await client.post('/telegram/webhook/sentinel',json={})).status_code==403
        assert (await client.post('/telegram/webhook/sentinel',headers={'X-Telegram-Bot-Api-Secret-Token':'fixture-webhook-secret'},json={})).status_code==422
        assert (await client.post('/deliveries/pending/drain',headers={'X-API-Key':'test-only-key'})).status_code==403
        assert (await client.post('/agents/asks/unknown',headers={'X-API-Key':'test-only-key'},json={'approved':True})).status_code==410
        saved=await client.put('/voice/agents/sentinel',headers={'X-API-Key':'test-only-key'},json={'voice_id':'azure:tr-TR-EmelNeural','expected_version':0})
        assert saved.status_code==200 and len(saved.json()['agents'])==8
    await bits.coordinator.shutdown()
