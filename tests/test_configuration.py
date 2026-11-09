import asyncio
import json
from pathlib import Path
import pytest
import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from app.config import Settings
from app.config_schema import catalog
from app.services.config_store import ConfigStore
from app.services.configuration import Configuration,PolicyConfiguration
from app.services.backend_restart import BackendRestart
from app.services.global_budget import GlobalBudget
from app.services.iacon_budget import MaintenanceBudget
from app.services.coordination_budget import CoordinationError
from app.middleware.auth import APIKeyMiddleware
from app.routers.config import router
from tests.test_coordination import setup,reply


def configuration(tmp_path,**kwargs):
    return Configuration(Settings(_env_file=None,**kwargs),store=ConfigStore(tmp_path/'configuration.json'))


def test_catalog_covers_every_actual_setting_once():
    fields=[f for g in catalog() for f in g['fields']]
    assert len(fields)==len(Settings.model_fields)==len({f['key'] for f in fields})
    assert {f['key'] for f in fields}==set(Settings.model_fields)
    assert next(f for f in fields if f['key']=='database_url')['secret']
    assert next(f for f in fields if f['key']=='telegram_bot_tokens')['secret']


async def test_atomic_settings_save_cas_clear_reset_and_startup_precedence(tmp_path,monkeypatch):
    service=configuration(tmp_path,openai_api_key='deployment-secret')
    value={'openai_api_key':'new-private-secret','owner_home_lat':41.23,'news_poll_enabled':False,'telegram_bot_tokens':'{"sentinel":"fixture-token"}'}
    value['optimus_github_workspace_token']='new-optimus-token'
    saved=await service.save(value,0,'save-one')
    assert saved['revision']==1 and not saved['applied_live'] and 'openai_api_key' in saved['restart_required']
    assert 'optimus_github_workspace_token' in saved['restart_required']
    encoded=json.dumps(service.snapshot())
    assert 'new-optimus-token' not in encoded
    assert 'new-private-secret' not in encoded and 'fixture-token' not in encoded and 'deployment-secret' not in encoded
    assert (await service.save(value,0,'save-one'))['duplicate']
    with pytest.raises(CoordinationError): await service.save({'debug':True},0,'stale')
    monkeypatch.setenv('SPEDA_DATA_DIR',str(tmp_path)); monkeypatch.setenv('OPENAI_API_KEY','environment-secret')
    monkeypatch.setenv('OPTIMUS_GITHUB_WORKSPACE_TOKEN','deployment-optimus-token')
    fresh=Settings(_env_file=None)
    assert fresh.optimus_github_workspace_token.get_secret_value()=='new-optimus-token'
    assert fresh.openai_api_key=='new-private-secret' and fresh.owner_home_lat==41.23 and fresh.news_poll_enabled is False
    assert Settings(_env_file=None,openai_api_key='explicit-init').openai_api_key=='explicit-init'
    fresh_service=Configuration(fresh,store=service.store)
    assert not fresh_service.snapshot()['restart_required']
    await fresh_service.save({'openai_api_key':'','optimus_github_workspace_token':''},1,'clear')
    assert Settings(_env_file=None).openai_api_key==''
    assert Settings(_env_file=None).optimus_github_workspace_token.get_secret_value()==''
    await fresh_service.save({},2,'reset',reset=['openai_api_key','optimus_github_workspace_token'])
    assert Settings(_env_file=None).optimus_github_workspace_token.get_secret_value()=='deployment-optimus-token'
    assert Settings(_env_file=None).openai_api_key=='environment-secret'
    assert 'openai_api_key' in fresh_service.snapshot()['restart_required']
    assert (tmp_path/'configuration-history'/'revision-1.json').exists()


async def test_invalid_save_is_wholly_rejected_and_does_not_echo_secret(tmp_path):
    service=configuration(tmp_path)
    for values in ({'openai_api_key':'VERY-PRIVATE','http_port':0},{'owner_home_lat':91},{'log_level':'made-up'},
        {'telegram_bot_tokens':'PRIVATE INVALID JSON'},{'owner_timezone':'No/SuchTimezone'},{'speda_api_key':''},
        {'openai_base_url':'http://[malformed'},{'openai_base_url':'not-a-url'},{'agent_language':'unsupported'}):
        with pytest.raises(CoordinationError) as error: await service.save(values,0,'invalid')
        assert 'VERY-PRIVATE' not in str(error.value) and 'PRIVATE INVALID JSON' not in str(error.value)
        assert service.store.read()['revision']==0
    with pytest.raises(CoordinationError): await service.save({'unknown_setting':True},0,'unknown')
    with pytest.raises(CoordinationError) as error:
        await service.save({'database_url':'PRIVATE database credential'},0,'invalid-database')
    assert 'PRIVATE database credential' not in str(error.value)


def test_private_urls_and_malformed_deployment_values_remain_repairable(tmp_path):
    service=configuration(tmp_path,openai_base_url='https://service.example/v1?key=PRIVATE-QUERY',
        gemini_base_url='http://[malformed')
    encoded=json.dumps(service.snapshot())
    assert 'PRIVATE-QUERY' not in encoded and 'malformed' not in encoded


async def test_simultaneous_writers_do_not_lose_updates(tmp_path):
    first=configuration(tmp_path); second=configuration(tmp_path)
    result=await asyncio.gather(first.save({'debug':True},0,'first'),second.save({'debug':False},0,'second'),return_exceptions=True)
    assert sum(isinstance(r,CoordinationError) for r in result)==1
    assert first.store.read()['revision']==1


def test_restart_receipt_is_not_forgotten_after_many_same_revision_restarts(tmp_path):
    store=ConfigStore(tmp_path/'configuration.json')
    for n in range(40): store.restart(0,f'restart-{n}',f'instance-{n}')
    assert store.restart(0,'restart-0','new-instance')['instance_id']=='instance-0'


async def test_negative_telegram_group_id_is_configurable(tmp_path):
    service=configuration(tmp_path)
    await service.save({'iacon_telegram_chat_id':-1001234567890},0,'group')
    assert service.store.read()['values']['iacon_telegram_chat_id']==-1001234567890


async def test_active_paid_work_blocks_restart_without_consuming_request(memory,tmp_path):
    from app.services.global_budget import GlobalCall
    bits=await setup(memory,[]); service=configuration(tmp_path)
    restart=BackendRestart(service,bits.coordinator,lambda:None)
    async with memory.sessions() as db,db.begin():
        db.add(GlobalCall(id='in-flight',user_id=1,day='2026-10-27',route='fixture',purpose='test',status='started',tokens=0,microdollars=0))
    with pytest.raises(CoordinationError,match='active work'): await restart.request(0,'busy-request')
    assert not restart.requested and not service.store.read()['restarts']
    async with memory.sessions() as db,db.begin():
        row=await db.get(GlobalCall,'in-flight'); row.status='succeeded'
    assert (await restart.request(0,'busy-request'))['accepted']
    budget=GlobalBudget(memory.sessions); budget.lifecycle=bits.coordinator
    with pytest.raises(CoordinationError,match='no paid call'):
        await budget.reserve_external(1,'fixture',1,'must-not-admit')
    assert (await budget.status(1))['lifetime_reserved']['calls']==1
    await bits.coordinator.shutdown()


def test_reply_language_and_iacon_timezone_follow_configuration(monkeypatch):
    from app.config import settings
    from app.core.orchestrator import AgentOrchestrator
    from app.profiles.speda import SPEDAProfile
    from app.routers.iacon import context
    monkeypatch.setattr(settings,'agent_language','tr')
    monkeypatch.setattr(settings,'owner_timezone','UTC')
    ctx=context()
    assert ctx.timezone=='UTC'
    prompt=AgentOrchestrator(None,None,SPEDAProfile()).build_system_prompt(ctx)
    assert 'Write user-facing prose in Turkish.' in prompt


def test_cleared_scheduler_secret_disables_iacon_scheduler(monkeypatch):
    from app.config import settings
    from app.routers.iacon import scheduler
    from fastapi import Request,HTTPException
    monkeypatch.setattr(settings,'n8n_secret','')
    with pytest.raises(HTTPException): scheduler(Request({'type':'http','headers':[]}))


async def test_restart_is_revision_bound_idle_only_and_not_replayed_after_boot(memory,tmp_path):
    bits=await setup(memory,[reply('Done')]); service=configuration(tmp_path)
    signalled=[]
    restart=BackendRestart(service,bits.coordinator,lambda:signalled.append(service.instance_id))
    await service.save({'debug':True},0,'save')
    with pytest.raises(CoordinationError): await restart.request(0,'old')
    result=await restart.request(1,'restart-once')
    assert result['accepted'] and not signalled
    await restart.signal()
    assert signalled==[service.instance_id] and bits.coordinator.restarting
    from app.schemas.coordination import CreateRoom,RoomInput
    room=await bits.coordinator.create(1,CreateRoom())
    with pytest.raises(CoordinationError,match='restarting'):
        await bits.coordinator.start(1,room['id'],RoomInput(input_id='new',message='Do not admit'))
    new=Configuration(service.settings,store=service.store)
    later=BackendRestart(new,bits.coordinator,lambda:signalled.append('should-not-run'))
    assert (await later.request(1,'restart-once'))['status']=='already_restarted'
    assert not later.requested
    await bits.coordinator.shutdown()


async def test_owner_api_auth_secrets_validation_and_policy_writers(memory,tmp_path):
    bits=await setup(memory,[]); config=configuration(tmp_path)
    app=FastAPI(); app.state.configuration=config
    app.state.backend_restart=BackendRestart(config,bits.coordinator)
    app.state.policy_configuration=PolicyConfiguration(bits.policy,bits.budget,GlobalBudget(memory.sessions),MaintenanceBudget(memory.sessions,bits.client))
    app.include_router(router); app.add_middleware(APIKeyMiddleware)
    @app.exception_handler(CoordinationError)
    async def error(request,exc): return JSONResponse(status_code=exc.status,content={'code':exc.code,'detail':str(exc)})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://fixture') as client:
        assert (await client.get('/config')).status_code==401
        client.headers['X-API-Key']='test-only-key'
        catalog_response=await client.get('/config')
        assert catalog_response.status_code==200 and catalog_response.json()['setting_count']==len(Settings.model_fields)
        assert (await client.put('/config',json={'values':'PRIVATE VALUE'})).status_code==422
        assert 'PRIVATE VALUE' not in (await client.put('/config',json={'values':'PRIVATE VALUE'})).text
        assert (await client.post('/config/restart',json={'expected_revision':0,'request_id':'once'})).status_code==503
        policies=(await client.get('/config/policies')).json()['policies']
        assert {p['id'] for p in policies}=={'models','coordination','global','iacon'}
        global_policy=next(p for p in policies if p['id']=='global')
        global_policy['value']['external_prices']={'speech:tts:openai:fixture':1}
        response=await client.put('/config/policies/global',json={'expected_revision':0,'value':global_policy['value']})
        assert response.status_code==200,response.text
        assert (await app.state.policy_configuration.global_budget.status(1))['policy']['external_prices']=={'speech:tts:openai:fixture':1}
    await bits.coordinator.shutdown()


async def test_all_four_policy_editors_use_native_cas_and_reject_unknown_fields(memory):
    bits=await setup(memory,[])
    service=PolicyConfiguration(bits.policy,bits.budget,GlobalBudget(memory.sessions),MaintenanceBudget(memory.sessions,bits.client))
    initial=(await service.snapshot(1))['policies']
    for policy in initial:
        saved=await service.save(1,policy['id'],policy['revision'],policy['value'])
        current=next(p for p in saved['policies'] if p['id']==policy['id'])
        assert current['revision']==policy['revision']+1 and current['value']==policy['value']
        assert set(current['schema']['properties'])==set(current['value'])
        with pytest.raises(CoordinationError):
            await service.save(1,policy['id'],policy['revision'],policy['value'])
        with pytest.raises(CoordinationError):
            await service.save(1,policy['id'],current['revision'],{**policy['value'],'unrecognized':True})
    await bits.coordinator.shutdown()


def test_supervisor_restarts_only_explicit_restart_exit(monkeypatch):
    from app.supervisor import supervise
    calls=[]; codes=iter([75,0])
    class Child:
        def __init__(self): self.code=next(codes)
        def wait(self,**kwargs): return self.code
        def poll(self): return self.code
    def launch(args,**kwargs): calls.append((args,kwargs)); return Child()
    assert supervise(launch)==0 and len(calls)==2
    assert all(c[0][-2:]==['-m','app.server'] for c in calls)


def test_supervisor_stop_has_a_bounded_owned_child_wait(monkeypatch):
    import signal
    import subprocess
    from app import supervisor
    waits=[]; handlers={}; sent=[]
    def install(sig,handler):
        old=handlers.get(sig,signal.SIG_DFL); handlers[sig]=handler; return old
    monkeypatch.setattr(signal,'signal',install)
    class Child:
        done=False
        def poll(self): return 0 if self.done else None
        def send_signal(self,sig): sent.append(sig)
        def wait(self,timeout=None):
            waits.append(timeout)
            if timeout==1:
                handlers[signal.SIGTERM](signal.SIGTERM,None)
                raise subprocess.TimeoutExpired('owned-child',timeout)
            self.done=True
            return 0
    assert supervisor.supervise(lambda *a,**k:Child())==0
    assert waits==[1,35] and len(sent)==1
