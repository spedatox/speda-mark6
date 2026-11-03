"""Owner configuration uses the native registry; models cannot edit connections."""
import asyncio
from datetime import datetime, timezone
import json
import re
import shlex
from pydantic import BaseModel, ConfigDict, Field
from app.config import settings
from app.mcp.client import MCPClient
from app.services.connection_settings import ConnectionSettings, MASKED

AGENTS = frozenset({'speda', 'ultron', 'optimus', 'friday', 'atomix', 'scourge', 'sentinel', 'orion'})
BUILTINS = frozenset({'notion', 'brave_search', 'fetch', 'alpha_vantage', 'tavily', 'exa',
    'github', 'filesystem', 'arxiv', 'cve_intelligence', 'playwright', 'google_gmail',
    'google_calendar', 'google_tasks', 'google_drive', 'google_people', 'google_chat', 'microsoft_outlook'})


class CustomConnection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(pattern=r'^[a-z][a-z0-9_]{0,39}$')
    transport: str
    command: str | list[str] = Field(default_factory=list)
    url: str = Field(default='', max_length=4096)
    env: dict[str, str] = Field(default_factory=dict, max_length=32)
    headers: dict[str, str] = Field(default_factory=dict, max_length=32)
    enabled: bool = True
    note: str = Field(default='', max_length=200)
    # Old clients omit grants: their owner-created connections default to Speda only.
    allowed_agents: list[str] = Field(default_factory=lambda: ['speda'], max_length=8)
    allowed_tools: list[str] | None = Field(default=None, max_length=1000)
    read_only_tools: list[str] = Field(default_factory=list, max_length=1000)
    added_at: str | None = None


def pinned_command(command):
    """Require an exact version for package-runner launchers, including UI entries."""
    if isinstance(command, str):
        command = json.loads(command) if command.lstrip().startswith('[') else shlex.split(command)
    if not command or len(command) > 32 or any(not isinstance(x, str) or len(x) > 4096 or '\x00' in x for x in command):
        raise ValueError('Use a bounded executable and argument list')
    runner = command[0].replace('\\', '/').split('/')[-1].lower().removesuffix('.cmd').removesuffix('.exe')
    if runner in {'npx', 'uvx'}:
        args = [x for x in command[1:] if not x.startswith('-')]
        package = args[0] if args else ''
        pattern = r'(.+)@(\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?)' if runner == 'npx' else r'(.+)==(\d+\.\d+\.\d+(?:[A-Za-z0-9.-]+)?)'
        if not re.fullmatch(pattern, package):
            raise ValueError('Package runners require an exact approved version (package@x.y.z or package==x.y.z)')
        before = command[1:command.index(package)]
        if any(flag not in ({'-y','--yes','--no-install'} if runner == 'npx' else {'--offline'}) for flag in before):
            raise ValueError('Use the pinned package directly, without package-runner overrides')
    return command


def build_custom(record):
    if record['transport'] not in {'stdio', 'http'}:
        raise ValueError('Use stdio or Streamable HTTP')
    if not set(record['allowed_agents']) <= AGENTS:
        raise ValueError('Unknown agent grant')
    if sum(len(k) + len(v) for field in ('env', 'headers') for k, v in record[field].items()) > 32000:
        raise ValueError('Credential fields exceed size limit')
    if record['transport'] == 'stdio':
        if record['url'] or record['headers']:
            raise ValueError('stdio accepts a command and env, not URL/headers')
        command = pinned_command(record['command'])
    else:
        if not record['url'] or record['command'] or record['env']:
            raise ValueError('HTTP accepts URL/headers, not command/env')
        command = None
        from urllib.parse import urlsplit
        if urlsplit(record['url']).query:
            raise ValueError('Store HTTP credentials in headers, not URL query strings')
    client = MCPClient(record['name'], record['transport'], command, record['url'] or None,
        env=record['env'], headers=record['headers'], allowed_agents=record['allowed_agents'],
        allowed_tools=record['allowed_tools'], read_only_tools=record['read_only_tools'])
    client.allowed_users = frozenset({1})
    return client


def clone_connection(client):
    """New transport lifetime on reconnect; only the app token provider is shared."""
    from app.mcp.rest_bridge import RestCapabilityClient
    from app.mcp.servers import NotionMCPClient
    if isinstance(client, NotionMCPClient):
        fresh = NotionMCPClient(client.token_provider)
    elif isinstance(client, RestCapabilityClient):
        fresh = RestCapabilityClient(client.server_name, client._tools, client._dispatch, client.token_provider)
    else:
        fresh = MCPClient(client.server_name, client.transport, client.command, client.url,
            env=dict(client.env), headers=dict(client.headers), auth=client.auth,
            allowed_agents=client.allowed_agents, read_only_tools=client.read_only_tools,
            allowed_tools=client.allowed_tools, request_timeout=client.request_timeout,
            concurrency=client.concurrency,
            connect_timeout=client.connect_timeout, max_tools=client.max_tools,
            max_result_bytes=client.max_result_bytes, target=client.target)
    fresh.allowed_agents = client.allowed_agents
    fresh.allowed_users = getattr(client, 'allowed_users', frozenset({1}))
    return fresh


class ConnectionManager:
    def __init__(self, registry, store=None, *, external_enabled=None):
        self.registry, self.store = registry, store or ConnectionSettings()
        self.external_enabled = settings.external_integrations_enabled if external_enabled is None else external_enabled
        self.lock = asyncio.Lock()
        self.errors = {}
        self.builtin_factories = {}
        self.reserved = set(BUILTINS) | set(registry._mcp_clients)
        if settings.mcp_connections_file:
            from app.mcp.configuration import connection_configs
            self.reserved.update(config.name for config in connection_configs(settings.mcp_connections_file))

    async def activate(self, name, client):
        # Drop first: a failed replacement must never leave old credentials live.
        await self.registry.drop_mcp_server(name)
        if not self.external_enabled:
            self.errors[name] = 'External integrations are disabled'
            return False
        try:
            if name in BUILTINS and client.transport == 'stdio':
                from app.mcp.servers import pin_builtin_command
                client.command = pin_builtin_command(client.command)
            client.active = True
            await self.registry.register_mcp(client)
            self.errors.pop(name, None)
            return True
        except Exception:
            await client.disconnect()
            self.errors[name] = 'Connection unavailable; check configuration and reconnect'
            return False

    async def startup(self):
        data = self.store.read()
        for name, client in list(self.registry._mcp_clients.items()):
            client.active = data.get('active', {}).get(name, True)
        for record in data.get('custom', {}).values():
            if record['name'] in self.reserved:
                raise ValueError('Custom MCP identity conflicts with operator configuration')
            if record['enabled'] and data.get('active', {}).get(record['name'], True):
                await self.activate(record['name'], build_custom(record))

    def summary(self):
        rows = []
        data = self.store.read()
        names = self.reserved | set(data.get('custom', {})) | set(self.errors)
        for name in sorted(names):
            client = self.registry._mcp_clients.get(name)
            status = client.status() if client else {'server': name, 'connected': False, 'tools': 0}
            enabled = set(settings.mcp_enabled.split(','))
            default_active = (data['custom'][name]['enabled'] if name in data.get('custom', {})
                else name in enabled or 'all' in enabled)
            rows.append({**status, 'active': getattr(client, 'active', data.get('active', {}).get(name, default_active)),
                'label': name.replace('_', ' ').title(), 'needs': None, 'tokens': 0,
                'always_on': False, 'error': self.errors.get(name)})
        return {'servers': rows, 'active_tool_tokens': 0, 'itpm_limit': None, 'lazy': True}

    def custom(self):
        rows = []
        live = {r['server']: r for r in self.summary()['servers']}
        for record in self.store.read().get('custom', {}).values():
            row = self.store.mask(record)
            row.update({k: live[record['name']].get(k) for k in ('connected', 'tools', 'tokens', 'error')})
            rows.append(row)
        return {'servers': rows, 'reserved': sorted(self.reserved)}

    async def save(self, body):
        async with self.lock:
            editable = {key:value for key,value in body.items() if key not in {'connected','tools','tokens','error'}}
            record = CustomConnection.model_validate(editable).model_dump()
            name = record['name']
            if name in self.reserved:
                raise ValueError('Built-in/operator connection identities cannot be replaced')
            data = self.store.read()
            records = data.setdefault('custom', {})
            if name not in records and len(records) >= 32:
                raise ValueError('At most 32 custom connections are supported')
            old = records.get(name, {})
            for field in ('env', 'headers'):
                for key, value in record[field].items():
                    if value == MASKED:
                        if key not in old.get(field, {}):
                            raise ValueError('Masked credential has no stored value')
                        record[field][key] = old[field][key]
            for field in ('allowed_agents', 'allowed_tools', 'read_only_tools'):
                if field not in body and field in old:
                    record[field] = old[field]
            client = build_custom(record)  # Validate before durable mutation or execution.
            record['added_at'] = old.get('added_at') or datetime.now(timezone.utc).isoformat()
            records[name] = record
            data.setdefault('active', {})[name] = record['enabled']
            self.store.write(data)
            if record['enabled']:
                connected = await self.activate(name, client)
            else:
                await self.registry.drop_mcp_server(name)
                self.errors.pop(name, None)
                connected = False
            return {'server': self.store.mask(record), 'connected': connected,
                'tools': len(client._tools) if connected else 0, 'error': self.errors.get(name)}

    async def remove(self, name):
        async with self.lock:
            if name in self.reserved:
                raise ValueError('Cannot delete a built-in/operator connection')
            data = self.store.read()
            existed = data.setdefault('custom', {}).pop(name, None) is not None
            data.setdefault('active', {}).pop(name, None)
            self.store.write(data)
            await self.registry.drop_mcp_server(name)
            self.errors.pop(name, None)
            return {'deleted': existed, 'server': name}

    async def toggle(self, name, active):
        async with self.lock:
            data = self.store.read()
            if name not in self.reserved and name not in data.get('custom', {}):
                raise ValueError('Unknown connection')
            data.setdefault('active', {})[name] = active
            record = data.get('custom', {}).get(name)
            if record:
                record['enabled'] = active
            self.store.write(data)
            client = self.registry._mcp_clients.get(name)
            if client:
                if getattr(client, 'active', True) != active:
                    client._admission_epoch += 1
                client.active = active
            elif active and record:
                await self.activate(name, build_custom(record))
            elif active and name in self.builtin_factories:
                await self.activate(name, self.builtin_factories[name]())
            if not active:
                self.errors.pop(name, None)
            return {'server': name, 'active': active, 'connected': bool(self.registry._mcp_clients.get(name) and self.registry._mcp_clients[name]._connected)}

    async def reconnect(self, name):
        async with self.lock:
            data = self.store.read()
            record = data.get('custom', {}).get(name)
            if not data.get('active', {}).get(name, True) or (record and not record['enabled']):
                raise ValueError('Enable the connection before reconnecting')
            client = build_custom(record) if record else self.builtin_factories[name]() if name in self.builtin_factories else None
            if not client:
                raise ValueError('Connection is not configured')
            connected = await self.activate(name, client)
            return {'server':name, 'connected':connected, 'error':self.errors.get(name)}
