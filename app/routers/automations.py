"""Owner control plane; the app-owned service owns validation and effects."""
from fastapi import APIRouter,Request,Query

router=APIRouter(tags=['automations'])


@router.get('/automations')
async def listing(request:Request):
    return {'automations':await request.app.state.automations.list(1)}


@router.get('/automations/agents')
async def agents(request:Request):
    return {'agents':[{k:p[k] for k in ('agent_id','name','domain')}
        for p in request.app.state.profiles.roster()]}


@router.get('/automations/status')
async def status(request:Request):
    service=request.app.state.automations
    telegram=await request.app.state.telegram_gateway.status(1)
    linked=any(bot['linked'] for bot in telegram['bots'])
    return {'n8n_configured':service.client.configured,'n8n_online':await service.client.ping(),
        'n8n_url':service.config.n8n_api_url if service.client.configured else '',
        'telegram_configured':telegram['configured'],'telegram_connected':linked,'delivery_status':'available' if linked else 'unconfigured'}


@router.post('/automations/telegram/connect')
async def telegram_connect(request:Request):
    return await request.app.state.telegram_gateway.link(1,'speda')


@router.get('/automations/telegram/status')
async def telegram_status(request:Request):
    result=await request.app.state.telegram_gateway.status(1)
    return result | {'connected':any(b['agent_id']=='speda' and b['linked'] for b in result['bots'])}


@router.post('/automations/telegram/disconnect')
async def telegram_disconnect(request:Request):
    return await request.app.state.telegram_gateway.disconnect(1,'speda')


@router.get('/automations/drift')
async def drift(request:Request):
    return await request.app.state.automations.drift(1)


@router.post('/automations')
async def create(request:Request,body:dict):
    service=request.app.state.automations
    created=await service.create(1,body)
    return await service.sync(1,created['id'])


@router.put('/automations/{identity}')
async def edit(identity:int,request:Request,body:dict):
    service=request.app.state.automations
    await service.edit(1,identity,body)
    return await service.sync(1,identity)


@router.post('/automations/{identity}/toggle')
async def toggle(identity:int,request:Request,body:dict):
    service=request.app.state.automations
    await service.edit(1,identity,{k:v for k,v in body.items() if k in {'active','expected_version'}})
    return await service.sync(1,identity)


@router.delete('/automations/{identity}')
async def delete(identity:int,request:Request,expected_version:int|None=None):
    service=request.app.state.automations
    await service.edit(1,identity,{'expected_version':expected_version} if expected_version is not None else {},remove=True)
    result=await service.sync(1,identity)
    return result | {'deleted':result['status']=='deleted'}


@router.post('/automations/{identity}/reconcile')
async def reconcile(identity:int,request:Request):
    return await request.app.state.automations.sync(1,identity,observe_only=True)


@router.post('/automations/{identity}/sync')
async def sync(identity:int,request:Request):
    return await request.app.state.automations.sync(1,identity)


@router.post('/automations/{identity}/test')
async def test(identity:int,request:Request):
    return await request.app.state.automations.test(1,identity)


@router.get('/automations/{identity}/runs')
async def runs(identity:int,request:Request,limit:int=Query(30,ge=1,le=30)):
    return {'runs':await request.app.state.automations.runs(1,identity,limit)}
