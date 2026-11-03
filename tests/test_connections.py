import asyncio
import hashlib
import base64
import json
import time
from urllib.parse import urlsplit, parse_qs, parse_qsl
import httpx
import pytest
from fastapi import FastAPI
from mcp.server import MCPServer
from sqlalchemy import update
from app.config import settings
from app.core.registry import CapabilityRegistry
from app.mcp.client import MCPClient
from app.models.oauth import OAuthAttempt
from app.services.connection_settings import ConnectionSettings, MASKED
from app.services.connections import ConnectionManager, pinned_command
from app.services.oauth import OAuthService
from app.schemas.llm import ProviderError


@pytest.fixture
def configured(tmp_path, monkeypatch):
    from app.core import runtime_state
    monkeypatch.setattr(runtime_state, '_STATE_FILE', tmp_path / 'runtime.json')
    monkeypatch.setattr(runtime_state, '_cache', None)
    for kind in ('google','microsoft','notion'):
        monkeypatch.setattr(settings, kind + '_client_id', 'fixture-client')
        monkeypatch.setattr(settings, kind + '_client_secret', 'fixture-secret')
    monkeypatch.setattr(settings, 'google_refresh_token', '')
    monkeypatch.setattr(settings, 'microsoft_refresh_token', '')
    monkeypatch.setattr(settings, 'notion_api_key', '')


def services(memory, tmp_path, *, enabled=True):
    registry = CapabilityRegistry(memory.sessions)
    manager = ConnectionManager(registry, ConnectionSettings(tmp_path / 'connections.json'), external_enabled=enabled)
    oauth = OAuthService(memory.sessions, manager)
    return registry, manager, oauth


async def test_oauth_browser_binding_pkce_replay_and_redaction(memory, tmp_path, configured, monkeypatch):
    from app.routers.connections import router
    from app.middleware.auth import APIKeyMiddleware
    from app.services import oauth as module
    original_client = httpx.AsyncClient
    registry, manager, oauth = services(memory, tmp_path)
    app = FastAPI()
    app.state.connections, app.state.oauth = manager, oauth
    app.include_router(router)
    app.add_middleware(APIKeyMiddleware)
    access_queries = []
    async def access_probe(scope, receive, send):
        async def checked(message):
            if message['type'] == 'http.response.start' and scope['path'].startswith('/oauth/'):
                access_queries.append(scope['query_string'])
            await send(message)
        await app(scope, receive, checked)
    exchanges = []
    def exchange(request):
        exchanges.append(dict(parse_qsl(request.content.decode())))
        return httpx.Response(200, json={'access_token':'secret-access', 'refresh_token':'secret-refresh', 'expires_in':3600})
    monkeypatch.setattr(module.httpx, 'AsyncClient', lambda **kw: original_client(transport=httpx.MockTransport(exchange), **kw))
    async def activate(provider): return 3
    monkeypatch.setattr(oauth, 'connect_vendor', activate)
    async with original_client(transport=httpx.ASGITransport(app=access_probe), base_url='http://localhost:8000') as client:
        assert (await client.get('/connections/google/login')).status_code == 401
        assert (await client.get('/oauth/google/callback')).status_code == 400
        assert (await client.get('/oauth/fake/callback')).status_code == 401
        login = await client.get('/connections/google/login', headers={'X-API-Key':'test-only-key'})
        start = await client.get(login.json()['auth_url'])
        assert start.status_code == 303 and 'HttpOnly' in start.headers['set-cookie']
        params = {k:v[0] for k,v in parse_qs(urlsplit(start.headers['location']).query).items()}
        assert params['code_challenge_method'] == 'S256'
        assert (await client.get(login.json()['auth_url'])).status_code == 400
        async with original_client(transport=httpx.ASGITransport(app=app), base_url='http://localhost:8000') as stranger:
            assert (await stranger.get('/oauth/google/callback', params={'state':params['state'],'code':'fixture'})).status_code == 400
        assert not exchanges
        response = await client.get('/oauth/google/callback', params={'state':params['state'],'code':'fixture'})
        assert response.status_code == 200 and 'no-store' in response.headers['cache-control']
        assert 'secret-access' not in response.text and 'secret-refresh' not in response.text
        challenge = base64.urlsafe_b64encode(hashlib.sha256(exchanges[0]['code_verifier'].encode()).digest()).decode().rstrip('=')
        assert challenge == params['code_challenge']
        assert (await client.get('/oauth/google/callback', params={'state':params['state'],'code':'fixture'})).status_code == 400
        assert len(exchanges) == 1
        assert oauth.status('google')['credential_present']
        assert (await client.post('/connections/google/disconnect', headers={'X-API-Key':'test-only-key'})).json() == {'connected':False}
        assert not oauth.status('google')['connected']
        # The outer server access logger must never receive codes or tickets.
        assert access_queries and all(not query for query in access_queries)


@pytest.mark.parametrize('provider', ['google','microsoft','notion'])
async def test_expired_wrong_provider_config_changed_denied_and_disconnect_invalidate(memory, tmp_path, configured, monkeypatch, provider):
    _, _, oauth = services(memory, tmp_path)
    async def begin():
        login = await oauth.login(provider)
        ticket = parse_qs(urlsplit(login['auth_url']).query)['ticket'][0]
        _, browser, _ = await oauth.start(provider, ticket)
        return ticket, browser
    state, browser = await begin()
    with pytest.raises(ValueError):
        await oauth.callback('google' if provider != 'google' else 'microsoft', state, browser, 'code','')
    async with memory.sessions() as db, db.begin():
        await db.execute(update(OAuthAttempt).values(expires_at=time.time() - 1))
    with pytest.raises(ValueError): await oauth.callback(provider,state,browser,'code','')
    state, browser = await begin()
    old = getattr(settings,provider+'_client_secret')
    monkeypatch.setattr(settings,provider+'_client_secret','changed')
    with pytest.raises(ValueError): await oauth.callback(provider,state,browser,'code','')
    monkeypatch.setattr(settings,provider+'_client_secret',old)
    state, browser = await begin()
    with pytest.raises(ValueError): await oauth.callback(provider,state,browser,'','<script>denied</script>')
    with pytest.raises(ValueError): await oauth.callback(provider,state,browser,'code','')
    state, browser = await begin()
    await oauth.disconnect(provider)
    with pytest.raises(ValueError): await oauth.callback(provider,state,browser,'code','')


async def test_unknown_exchange_is_consumed_and_never_retried(memory,tmp_path,configured,monkeypatch):
    from app.services import oauth as module
    _, _, oauth = services(memory,tmp_path)
    login = await oauth.login('microsoft')
    state = parse_qs(urlsplit(login['auth_url']).query)['ticket'][0]
    _, browser, _ = await oauth.start('microsoft',state)
    calls=[]
    original=httpx.AsyncClient
    def fail(request):
        calls.append(request)
        raise httpx.ReadTimeout('sensitive response must never appear')
    monkeypatch.setattr(module.httpx,'AsyncClient',lambda **kw: original(transport=httpx.MockTransport(fail), **kw))
    with pytest.raises(ValueError,match='start sign-in again'):
        await oauth.callback('microsoft',state,browser,'code','')
    with pytest.raises(ValueError,match='already used'):
        await oauth.callback('microsoft',state,browser,'code','')
    assert len(calls)==1 and not oauth.status('microsoft')['connected']


async def test_custom_masked_edits_disable_reload_and_failed_replacement_revoke(memory,tmp_path,monkeypatch):
    import app.services.connections as module
    registry, manager, _ = services(memory,tmp_path)
    server=MCPServer('fixture')
    @server.tool()
    async def lookup(query:str)->str: return query
    real_build=module.build_custom
    fail=False
    def build(record):
        real_build(record)  # Exercise validation without a real remote endpoint.
        client=MCPClient(record['name'],target=server,allowed_agents=record['allowed_agents'])
        client.allowed_users=frozenset({1})
        if fail:
            async def broken(): raise RuntimeError('secret-credential')
            client.connect=broken
        return client
    monkeypatch.setattr(module,'build_custom',build)
    body={'name':'fixture','transport':'http','url':'https://fixture.invalid/mcp','headers':{'Authorization':'secret-credential'}}
    try:
        saved=await manager.save(body)
        assert saved['connected'] and 'secret-credential' not in json.dumps(saved)
        context=memory.context(agent_id='speda')
        assert registry.search_tools('lookup',context)
        assert not registry.search_tools('lookup',memory.context(user_id=2))
        masked=manager.custom()['servers'][0]
        assert masked['headers']['Authorization']==MASKED
        assert 'secret-credential' not in json.dumps(manager.summary())
        editable={key:masked[key] for key in body}
        await manager.save(editable)
        assert manager.store.read()['custom']['fixture']['headers']['Authorization']=='secret-credential'
        assert not registry.search_tools('lookup',context) # Same schema/name, different connection.
        assert registry.search_tools('lookup',memory.context())
        fail=True
        saved=await manager.save(editable)
        assert not saved['connected'] and 'secret-credential' not in json.dumps(saved)
        assert not registry.search_tools('lookup',memory.context())
        assert manager.custom()['servers'][0]['error']
        fail=False
        assert (await manager.reconnect('fixture'))['connected']
        await manager.toggle('fixture',False)
        assert not registry.search_tools('lookup',memory.context())
        assert not manager.store.read()['custom']['fixture']['enabled']
        other=ConnectionManager(CapabilityRegistry(memory.sessions),ConnectionSettings(manager.store.path),external_enabled=False)
        await other.startup()
        assert not registry.search_tools('lookup',memory.context())
        await manager.remove('fixture')
        assert not manager.custom()['servers']
        with pytest.raises(ValueError): await manager.save({**body,'name':'notion'})
    finally:
        await registry.shutdown_adapters()


@pytest.mark.parametrize('body', [
    {'transport':'stdio','command':'npx -y latest-package'},
    {'transport':'http','url':'http://public.invalid/mcp','headers':{'Authorization':'secret'}},
    {'transport':'http','url':'https://fixture.invalid/mcp?token=secret'},
    {'transport':'http','url':'https://fixture.invalid/mcp','allowed_agents':['unknown']},
])
async def test_invalid_custom_config_never_persists_or_executes(memory,tmp_path,body):
    _, manager, _=services(memory,tmp_path)
    with pytest.raises(ValueError): await manager.save({'name':'fixture',**body})
    assert not manager.store.path.exists()


async def test_external_disable_preserves_config_without_connecting(memory,tmp_path,configured):
    _, manager, oauth=services(memory,tmp_path,enabled=False)
    result=await manager.save({'name':'fixture','transport':'http','url':'https://fixture.invalid/mcp'})
    assert not result['connected'] and 'disabled' in result['error']
    with pytest.raises(ValueError,match='disabled'): await oauth.login('google')


def test_exact_package_pins_required(monkeypatch):
    from app.mcp.servers import pin_builtin_command
    assert pinned_command('npx -y @fixture/server@1.2.3')[-1]=='@fixture/server@1.2.3'
    with pytest.raises(ValueError): pinned_command('npx -y @fixture/server@latest')
    monkeypatch.setattr(settings,'mcp_package_pins','{}')
    with pytest.raises(ValueError): pin_builtin_command(['npx','-y','tavily-mcp'])
    monkeypatch.setattr(settings,'mcp_package_pins','{"tavily-mcp":"1.2.3"}')
    assert pin_builtin_command(['npx','-y','tavily-mcp'])[-1]=='tavily-mcp@1.2.3'


@pytest.mark.parametrize('provider', ['google','microsoft','notion'])
async def test_success_survives_service_restart_and_concurrent_callback_exchanges_once(memory,tmp_path,configured,monkeypatch,provider):
    from app.services import oauth as module
    _, manager, oauth=services(memory,tmp_path)
    login=await oauth.login(provider)
    state=parse_qs(urlsplit(login['auth_url']).query)['ticket'][0]
    _,browser,_=await oauth.start(provider,state)
    restarted=OAuthService(memory.sessions,manager)
    calls=[]
    original=httpx.AsyncClient
    def exchange(request):
        calls.append(request)
        if provider=='notion':
            assert request.headers['authorization'].startswith('Basic ')
            assert json.loads(request.content)['grant_type']=='authorization_code'
        else:
            assert dict(parse_qsl(request.content.decode()))['code_verifier']
        return httpx.Response(200,json={'access_token':'fixture-access','refresh_token':'fixture-refresh','expires_in':3600})
    monkeypatch.setattr(module.httpx,'AsyncClient',lambda **kw: original(transport=httpx.MockTransport(exchange),**kw))
    async def connect(kind): return 0
    monkeypatch.setattr(restarted,'connect_vendor',connect)
    results=await asyncio.gather(*(restarted.callback(provider,state,browser,'fixture-code','') for _ in range(2)),return_exceptions=True)
    assert sum(isinstance(result,str) for result in results)==1 and len(calls)==1
    assert restarted.status(provider)['credential_present']


async def test_resources_cannot_use_replaced_or_disabled_connection(memory):
    from app.skills.mcp_data import MCPDataSkill
    server=MCPServer('resources')
    @server.resource('fixture://evidence')
    async def evidence()->str: return 'Original evidence'
    registry=CapabilityRegistry(memory.sessions)
    skill=MCPDataSkill(registry)
    try:
        first=MCPClient('fixture',target=server,allowed_agents={'speda'})
        await registry.register_mcp(first)
        context=memory.context()
        assert skill.available(context)
        await registry.drop_mcp_server('fixture')
        await registry.register_mcp(MCPClient('fixture',target=server,allowed_agents={'speda'}))
        with pytest.raises(ProviderError):
            await skill.execute({'server':'fixture','operation':'read_resource','uri':'fixture://evidence'},context)
        new=memory.context()
        assert 'Original evidence' in await skill.execute({'server':'fixture','operation':'read_resource','uri':'fixture://evidence'},new)
        registry._mcp_clients['fixture'].active=False
        with pytest.raises(ProviderError): await skill.execute({'server':'fixture','operation':'list_resources'},new)
    finally:
        await registry.shutdown_adapters()


async def test_disabled_builtin_does_not_handshake_on_startup_or_lose_grants(memory,configured,monkeypatch):
    from app.mcp.servers import register_all_mcp_servers
    calls=[]
    async def connect(client):
        calls.append(client.server_name)
        client._connected=True
    monkeypatch.setattr(settings,'mcp_enabled','fetch')
    monkeypatch.setattr(settings,'mcp_package_pins','{"@modelcontextprotocol/server-fetch":"1.2.3"}')
    monkeypatch.setattr(MCPClient,'connect',connect)
    registry=CapabilityRegistry(memory.sessions)
    await register_all_mcp_servers(registry,active={'fetch':False})
    assert not calls and not registry._mcp_clients
    assert registry._configured_mcp['fetch'].allowed_agents


async def test_notion_refresh_rotation_and_expired_process_require_reconnect(tmp_path,configured,monkeypatch):
    from app.core import runtime_state
    from app.services.notion_auth import NotionToken
    from app.mcp.servers import NotionMCPClient
    original=httpx.AsyncClient
    calls=[]
    def exchange(request):
        calls.append(request)
        return httpx.Response(200,json={'access_token':'new-access','refresh_token':'new-refresh','expires_in':3600})
    monkeypatch.setattr('app.services.notion_auth.httpx.AsyncClient',lambda **kw: original(transport=httpx.MockTransport(exchange),**kw))
    runtime_state.set_notion_credentials({'access_token':'old-access','refresh_token':'old-refresh','expires_at':time.time()-1})
    token=NotionToken()
    assert await asyncio.gather(*(token.get() for _ in range(4)))==['new-access']*4
    assert len(calls)==1 and runtime_state.get_notion_credentials()['refresh_token']=='new-refresh'
    client=NotionMCPClient(token)
    client.env={'NOTION_TOKEN':'old-access'}
    client._connected=True
    with pytest.raises(ProviderError,match='reconnect'): await client._check_credential()
    assert not client._connected


async def test_connection_management_api_auth_full_row_echo_and_strict_toggle(memory,tmp_path):
    from app.routers.connections import router
    from app.routers.mcp import router as mcp_router
    from app.middleware.auth import APIKeyMiddleware
    registry,manager,oauth=services(memory,tmp_path,enabled=False)
    app=FastAPI()
    app.state.registry,app.state.connections,app.state.oauth=registry,manager,oauth
    app.include_router(router)
    app.include_router(mcp_router)
    app.add_middleware(APIKeyMiddleware)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://fixture') as client:
        assert (await client.get('/connections')).status_code==401
        assert (await client.post('/connections/mcp',json={})).status_code==401
        client.headers['X-API-Key']='test-only-key'
        payload={'name':'fixture','transport':'http','url':'https://fixture.invalid/mcp',
                 'headers':{'Authorization':'never-echo-this-token'}}
        assert not (await client.post('/connections/mcp',json=payload)).json()['connected']
        row=(await client.get('/connections/mcp')).json()['servers'][0]
        assert row['headers']['Authorization']==MASKED
        assert (await client.post('/connections/mcp',json={**row,'enabled':False})).json()['error'] is None
        assert (await client.post('/connections',json={'server':'fixture','active':'false'})).status_code==422
        invalid=await client.post('/connections/mcp',json={**payload,'allowed_agents':['never-echo-this-token']})
        assert 'never-echo-this-token' not in invalid.text and invalid.json()['error']
        assert (await client.get('/connections')).json()['itpm_limit'] is None
        assert (await client.post('/mcp/servers/fixture/reconnect')).status_code==400
        assert (await client.delete('/connections/mcp/fixture')).json()['deleted']


async def test_operator_names_reserved_while_external_integrations_are_off(memory,tmp_path,monkeypatch):
    config=tmp_path/'operator.json'
    config.write_text(json.dumps([{'name':'operator_server','transport':'stdio',
        'command':['fixture-executable'],'allowed_agents':['speda'],
        'env_from':{'TOKEN':'DELIBERATELY_MISSING_FIXTURE_ENV'}}]),encoding='utf-8')
    monkeypatch.setattr(settings,'mcp_connections_file',str(config))
    _,manager,_=services(memory,tmp_path,enabled=False)
    assert 'operator_server' in manager.custom()['reserved']
    with pytest.raises(ValueError,match='identities'):
        await manager.save({'name':'operator_server','transport':'http','url':'https://fixture.invalid/mcp'})


@pytest.mark.parametrize('operation',['tool','resource'])
async def test_revocation_while_waiting_for_capacity_never_dispatches(operation):
    server=MCPServer('queued')
    sent=[]
    @server.tool()
    async def effect()->str:
        sent.append('tool')
        return 'done'
    @server.resource('fixture://queued')
    async def source()->str:
        sent.append('resource')
        return 'evidence'
    entered,release=asyncio.Event(),asyncio.Event()
    class Gate:
        async def __aenter__(self):
            entered.set()
            await release.wait()
        async def __aexit__(self,*args): pass
    client=MCPClient('fixture',target=server)
    await client.connect()
    client._slots=Gate()
    try:
        pending=asyncio.create_task(client.call_tool('effect',{}) if operation=='tool' else client.request('read_resource',uri='fixture://queued'))
        await entered.wait()
        client._admission_epoch+=1
        client.active=False
        client.active=True  # A later re-enable cannot revive a queued effect.
        release.set()
        with pytest.raises(ProviderError,match='before dispatch'): await pending
        assert not sent
    finally:
        release.set()
        await client.disconnect()


async def test_disconnect_during_token_refresh_prevents_rest_dispatch():
    from app.mcp.rest_bridge import RestCapabilityClient
    entered,release=asyncio.Event(),asyncio.Event()
    class Token:
        block=False
        async def get(self):
            if self.block:
                entered.set()
                await release.wait()
            return 'fixture-access'
    sent=[]
    async def dispatch(*args):
        sent.append(args)
        return 'done'
    token=Token()
    client=RestCapabilityClient('fixture',[{'name':'effect','input_schema':{'type':'object'}}],dispatch,token)
    await client.connect()
    token.block=True
    pending=asyncio.create_task(client.call_tool('effect',{}))
    await entered.wait()
    await client.disconnect()
    release.set()
    with pytest.raises(ProviderError,match='before dispatch'): await pending
    assert not sent
