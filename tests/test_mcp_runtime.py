import asyncio
import json
import pytest
from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from app.mcp.client import MCPClient, fingerprint
from app.core.registry import CapabilityRegistry
from app.schemas.llm import Block, ProviderError
from sqlalchemy import select
from app.models.model_policy import ToolExecutionReceipt


async def test_sdk_discovery_structured_resources_namespace_and_authority(memory):
    server = MCPServer("fixture")
    @server.tool(structured_output=True)
    async def lookup(query: str) -> dict[str, str]:
        return {"answer": query}
    @server.resource("fixture://facts")
    async def facts() -> str:
        return "data, not instructions"
    @server.prompt()
    async def inspect(topic: str) -> str:
        return "Inspect " + topic
    client = MCPClient("fixture", target=server, allowed_agents={"speda"}, read_only_tools={"lookup"})
    registry = CapabilityRegistry(memory.sessions)
    try:
        await registry.register_mcp(client)
        assert client.status()["connected"]
        context = memory.context(agent_id="speda", allowed_tools=frozenset())
        assert registry.list_tools(context) == []  # progressive discovery
        found = registry.search_tools("lookup", context)
        assert len(found) == 1 and found[0]["name"].startswith("mcp_fixture_lookup_")
        name = found[0]["name"]
        context.extra['loaded_tools'] = {name}
        result = json.loads(await registry.execute_recorded(Block(id="call1", name=name, input={"query": "source"}), context))
        assert result['structuredContent'] == {"answer": "source"}
        assert json.loads(await client.request('read_resource', uri='fixture://facts'))['contents'][0]['text'] == 'data, not instructions'
        assert json.loads(await client.request('get_prompt', name='inspect', arguments={'topic':'data'}))['messages'][0]['role'] == 'user'
        # Namespace collisions never overwrite another server's tool.
        other = MCPClient("other", target=server, allowed_agents={"speda"})
        await registry.register_mcp(other)
        assert len(registry.search_tools('lookup', context)) == 1  # original grant snapshot
        assert len(registry.search_tools('lookup', memory.context())) == 2
        assert registry.search_tools('lookup', memory.context(agent_id='ultron')) == []
    finally:
        await registry.shutdown_adapters()
    assert not client._connected and client._task.done()


async def test_untrusted_read_annotation_cannot_authorize_worker(memory):
    server = MCPServer("untrusted")
    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    async def deceptive() -> str:
        raise AssertionError("Not an authorized read")
    client = MCPClient("untrusted", target=server, allowed_agents={"speda"})
    registry = CapabilityRegistry()
    try:
        await registry.register_mcp(client)
        assert registry.search_tools('deceptive', memory.context(task_role='worker')) == []
    finally:
        await registry.shutdown_adapters()


async def test_cancelled_effect_retains_unknown_receipt_and_never_replays(memory):
    started = asyncio.Event()
    server = MCPServer("slow")
    @server.tool()
    async def effect() -> str:
        started.set()
        await asyncio.sleep(60)
        return "done"
    client = MCPClient("slow", target=server, allowed_agents={"speda"})
    registry = CapabilityRegistry(memory.sessions)
    try:
        await registry.register_mcp(client)
        context = memory.context()
        name = registry.search_tools('effect', context)[0]['name']
        context.extra['loaded_tools'] = {name}
        call = Block(id='one-effect', name=name, input={})
        task = asyncio.create_task(registry.execute_recorded(call, context))
        await asyncio.wait_for(started.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(ProviderError, match='unknown outcome'):
            await registry.execute_recorded(call, context)
        assert client.calls == 1
        async with memory.sessions() as db:
            receipt = await db.scalar(select(ToolExecutionReceipt))
            assert receipt.status == 'started'
    finally:
        await registry.shutdown_adapters()


async def test_changed_schema_and_large_result_are_bounded(memory):
    server = MCPServer("bounds")
    @server.tool()
    async def lookup() -> str:
        return 'x' * 10000
    client = MCPClient("bounds", target=server, allowed_agents={"speda"}, max_result_bytes=2000)
    try:
        await client.connect()
        with pytest.raises(ProviderError, match='changed'):
            await client.call_tool('lookup', {}, expected_schema='wrong')
        assert client.calls == 0
        result = await client.call_tool('lookup', {}, expected_schema=fingerprint(client._tools[0]))
        assert len(result.encode()) < 2000 and json.loads(result)['truncated']
    finally:
        await client.disconnect()


def test_http_credentials_require_tls_and_no_url_userinfo():
    with pytest.raises(ValueError):
        MCPClient('invalid', transport='http', url='https://user:password@example.com')
    with pytest.raises(ValueError):
        MCPClient('invalid', transport='http', url='http://example.com', headers={'Authorization':'Bearer secret'})


async def test_real_stdio_transport_does_not_inherit_provider_secrets(tmp_path, monkeypatch):
    import sys
    monkeypatch.setenv('OPENAI_API_KEY', 'fixture-credential-must-not-cross-process')
    path = tmp_path / 'server.py'
    path.write_text('''import os
from mcp.server import MCPServer
server = MCPServer('stdio-fixture')
@server.tool()
def environment_check() -> str:
    return 'leaked' if os.environ.get('OPENAI_API_KEY') else 'isolated'
server.run(transport='stdio')
''')
    client = MCPClient('stdio_fixture', command=[sys.executable, '-B', str(path)], allowed_agents={'speda'})
    try:
        await client.connect()
        result = json.loads(await client.call_tool('environment_check', {}))
        assert 'isolated' in json.dumps(result) and 'leaked' not in json.dumps(result)
    finally:
        await client.disconnect()
    assert client._task.done()


async def test_real_streamable_http_negotiates_current_protocol():
    import socket
    import uvicorn
    server = MCPServer('http-fixture')
    @server.tool()
    async def ping_data() -> str:
        return 'wire-ok'
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen()
    port = listener.getsockname()[1]
    host = uvicorn.Server(uvicorn.Config(server.streamable_http_app(), host='127.0.0.1', port=port,
        log_level='error', lifespan='on'))
    host_task = asyncio.create_task(host.serve(sockets=[listener]))
    client = MCPClient('http_fixture', transport='http', url=f'http://127.0.0.1:{port}/mcp', allowed_agents={'speda'})
    try:
        async with asyncio.timeout(5):
            while not host.started:
                if host_task.done():
                    await host_task
                await asyncio.sleep(.01)
        await client.connect()
        assert client.protocol_version == '2026-07-28'
        result = json.loads(await client.call_tool('ping_data', {}))
        assert 'wire-ok' in json.dumps(result)
    finally:
        await client.disconnect()
        host.should_exit = True
        await asyncio.wait_for(host_task, 5)
        listener.close()


async def test_sdk_schema_rejection_never_automatically_relists_and_retries(monkeypatch):
    from mcp import MCPError
    server = MCPServer('rejection-fixture')
    @server.tool()
    async def effect() -> str:
        return 'should not execute'
    client = MCPClient('rejection_fixture', target=server, allowed_agents={'speda'})
    calls = []
    try:
        await client.connect()
        async def reject(*args, **kwargs):
            calls.append(args)
            raise MCPError(-32020, 'Header mismatch')
        monkeypatch.setattr(client._client.session, 'call_tool', reject)
        with pytest.raises(ProviderError):
            await client.call_tool('effect', {})
        assert len(calls) == 1
    finally:
        await client.disconnect()
