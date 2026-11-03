import asyncio
import json
import httpx
import pytest
from app.config import settings
from app.core.registry import CapabilityRegistry
from app.schemas.llm import Block, ProviderError
from app.services.google_auth import GoogleToken
from app.mcp.google_rest import build_google_clients
from app.mcp.microsoft_rest import build_microsoft_clients, _Token


@pytest.fixture
def credentials(tmp_path, monkeypatch):
    from app.core import runtime_state
    monkeypatch.setattr(runtime_state, '_STATE_FILE', tmp_path / 'state.json')
    monkeypatch.setattr(runtime_state, '_cache', None)
    for kind in ('google', 'microsoft'):
        monkeypatch.setattr(settings, kind + '_client_id', 'fixture-client')
        monkeypatch.setattr(settings, kind + '_client_secret', 'fixture-secret')
        monkeypatch.setattr(settings, kind + '_refresh_token', 'fixture-refresh')


@pytest.mark.parametrize('kind', ['google', 'microsoft'])
async def test_token_refresh_is_serialized_rotation_persists_and_disconnect_invalidates(credentials, monkeypatch, kind):
    from app.core import runtime_state
    calls = []
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            calls.append(kwargs)
            await asyncio.sleep(0)
            return httpx.Response(200, json={'access_token':'fixture-access', 'expires_in':3600,
                                           'refresh_token':'rotated-refresh'})
    monkeypatch.setattr('app.services.google_auth.httpx.AsyncClient', Client)
    token = GoogleToken() if kind == 'google' else _Token()
    assert await asyncio.gather(*(token.get() for _ in range(8))) == ['fixture-access'] * 8
    assert len(calls) == 1
    if kind == 'microsoft':
        assert runtime_state.get_microsoft_refresh_token() == 'rotated-refresh'
        monkeypatch.setattr(runtime_state, '_cache', None)
        assert runtime_state.get_microsoft_refresh_token() == 'rotated-refresh'
    getattr(runtime_state, 'set_' + kind + '_refresh_token')('')
    assert await token.get() is None and len(calls) == 1


async def test_rest_connections_share_native_grants_and_receipts(memory, credentials, monkeypatch):
    class Token:
        async def get(self): return 'fixture-token'
    clients = build_google_clients(token_provider=Token()) + build_microsoft_clients(token_provider=Token())
    registry = CapabilityRegistry(memory.sessions)
    dispatched = []
    for client in clients:
        client.allowed_agents = frozenset({'speda', 'friday'})
        async def dispatch(name, args, token):
            dispatched.append((name, token))
            return 'Confirmed fixture result'
        client._dispatch = dispatch
        await registry.register_mcp(client)
    assert len(registry._mcp_definitions) == 30
    context = memory.context(agent_id='speda')
    found = registry.search_tools('gmail search', context)
    selected = next(d for d in found if 'gmail_search' in d['name'])
    context.extra['loaded_tools'] = {selected['name']}
    call = Block(type='tool_use', id='search', name=selected['name'], input={'query':'subject:fixture'})
    assert await registry.execute_recorded(call, context) == 'Confirmed fixture result'
    assert await registry.execute_recorded(call, context) == 'Confirmed fixture result'
    assert len(dispatched) == 1
    assert not registry.search_tools('gmail', memory.context(agent_id='atomix'))
    assert not registry.search_tools('gmail', memory.context(agent_id='speda', user_id=2))
    worker = memory.context(agent_id='speda', task_role='worker')
    assert registry.search_tools('gmail search', worker)
    assert not any('gmail_send' in t['name'] for t in registry.search_tools('gmail send', worker))
    send_name = next(n for n, original in registry._mcp_original_names.items() if original == 'gmail_send')
    context.extra['loaded_tools'].add(send_name)
    client = registry._mcp_clients['google_gmail']
    async def uncertain(*args):
        raise httpx.ReadTimeout('after dispatch')
    client._dispatch = uncertain
    send = Block(type='tool_use', id='send', name=send_name,
        input={'to':'fixture@example.test', 'subject':'Fixture', 'body':'Fixture'})
    with pytest.raises(ProviderError, match='inspect'):
        await registry.execute_recorded(send, context)
    with pytest.raises(ProviderError, match='unknown outcome'):
        await registry.execute_recorded(send, context)
    await registry.shutdown_adapters()


async def test_inherited_dispatch_shapes_remain_compatible(monkeypatch):
    import app.mcp.google_rest as google
    import app.mcp.microsoft_rest as microsoft
    calls = []
    async def request(method, url, token, **kwargs):
        calls.append((method, url, kwargs))
        if '/calendars/' in url:
            body = {'items':[]}
        else:
            body = {'value':[]}
        return httpx.Response(200, json=body, request=httpx.Request(method, url))
    monkeypatch.setattr(google, '_req', request)
    monkeypatch.setattr(microsoft, '_req', request)
    assert isinstance(await google._calendar_dispatch('calendar_list_events', {'calendar_id':'primary'}, 'fixture'), str)
    assert isinstance(await microsoft._outlook_dispatch('outlook_search', {'query':'fixture'}, 'fixture'), str)
    assert len(calls) == 2 and all(c[0] == 'GET' for c in calls)
