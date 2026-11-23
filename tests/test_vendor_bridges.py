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


async def test_rest_definite_4xx_and_read_only_errors_return_tool_error_not_unknown_outcome(memory, credentials):
    class Token:
        async def get(self): return 'fixture-token'
    clients = build_google_clients(token_provider=Token())
    registry = CapabilityRegistry(memory.sessions)
    for client in clients:
        client.allowed_agents = frozenset({'speda'})
        await registry.register_mcp(client)
    context = memory.context(agent_id='speda')
    tasks_list_name = next(n for n, original in registry._mcp_original_names.items() if original == 'tasks_list')
    gmail_send_name = next(n for n, original in registry._mcp_original_names.items() if original == 'gmail_send')
    context.extra['loaded_tools'] = {tasks_list_name, gmail_send_name}

    tasks_client = registry._mcp_clients['google_tasks']
    req = httpx.Request('GET', 'https://tasks.googleapis.com/tasks/v1/lists/Mark%20VI/tasks')
    resp_400 = httpx.Response(400, json={'error': {'code': 400, 'message': 'Invalid task list ID'}}, request=req)
    async def bad_list(name, args, token):
        resp_400.raise_for_status()
    tasks_client._dispatch = bad_list
    out = await registry.execute_recorded(
        Block(type='tool_use', id='t-bad-list', name=tasks_list_name, input={'tasklist_id': 'Mark VI'}),
        context,
    )
    assert out == 'Error: Vendor rejected tasks_list (HTTP 400): Invalid task list ID'

    async def read_timeout(name, args, token):
        raise httpx.ReadTimeout('read timed out')
    tasks_client._dispatch = read_timeout
    out_timeout = await registry.execute_recorded(
        Block(type='tool_use', id='t-read-timeout', name=tasks_list_name, input={'tasklist_id': '@default'}),
        context,
    )
    assert out_timeout == 'Error: Vendor request timed out for tasks_list.'

    gmail_client = registry._mcp_clients['google_gmail']
    send_req = httpx.Request('POST', 'https://gmail.googleapis.com/gmail/v1/users/me/messages/send')
    resp_400_send = httpx.Response(400, json={'error': {'message': 'Invalid To header'}}, request=send_req)
    async def bad_send(name, args, token):
        resp_400_send.raise_for_status()
    gmail_client._dispatch = bad_send
    out_send = await registry.execute_recorded(
        Block(type='tool_use', id='t-send-400', name=gmail_send_name,
              input={'to': 'not-an-email', 'subject': 'Hi', 'body': 'Test'}),
        context,
    )
    assert out_send == 'Error: Vendor rejected gmail_send (HTTP 400): Invalid To header'

    # 5xx and 408 on mutating tools remain unknown outcomes requiring reconciliation.
    for code, call_id in ((500, 't-send-500'), (408, 't-send-408')):
        resp_uncertain = httpx.Response(code, json={'error': {'message': 'transient'}}, request=send_req)
        async def uncertain_send(name, args, token, _r=resp_uncertain):
            _r.raise_for_status()
        gmail_client._dispatch = uncertain_send
        with pytest.raises(ProviderError, match='inspect'):
            await registry.execute_recorded(
                Block(type='tool_use', id=call_id, name=gmail_send_name,
                      input={'to': 'ok@example.test', 'subject': 'Hi', 'body': 'Test'}),
                context,
            )

    await registry.shutdown_adapters()
