"""Operator configuration, never model-provided commands or credential values."""
import json
import os
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.mcp.client import MCPClient


class Connection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(pattern=r'^[a-z][a-z0-9_]{0,39}$')
    transport: Literal['stdio', 'http']
    command: list[str] = Field(default_factory=list, max_length=32)
    url: str | None = None
    allowed_agents: list[str] = Field(max_length=8)
    allowed_tools: list[str] | None = None
    read_only_tools: list[str] = Field(default_factory=list)
    env_from: dict[str, str] = Field(default_factory=dict)
    headers_from: dict[str, str] = Field(default_factory=dict)
    timeout: float = Field(default=30, ge=1, le=300)
    concurrency: int = Field(default=4, ge=1, le=16)

    @model_validator(mode='after')
    def valid_transport(self):
        if (self.transport == 'http' and (not self.url or self.command)) or (self.transport == 'stdio' and (not self.command or self.url)):
            raise ValueError('Choose an HTTP URL or an explicit stdio command')
        if not set(self.allowed_agents) <= {'speda','ultron','optimus','friday','atomix','scourge','sentinel','orion'}:
            raise ValueError('Unknown agent grant')
        return self


def connection_configs(path):
    data = Path(path).read_bytes()
    if len(data) > 128000:
        raise ValueError('MCP configuration exceeds size limit')
    records = json.loads(data)
    if not isinstance(records, list) or len(records) > 32:
        raise ValueError('Expected at most 32 MCP connections')
    configs, names = [], set()
    for record in records:
        config = Connection.model_validate(record)
        if config.name in names:
            raise ValueError('Duplicate MCP connection identity')
        names.add(config.name)
        configs.append(config)
    return configs


def load_connections(path):
    clients = []
    for config in connection_configs(path):
        def secrets(mapping):
            if any(name not in os.environ for name in mapping.values()):
                raise ValueError('Required MCP credential environment variable is missing')
            return {key: os.environ[name] for key, name in mapping.items()}
        clients.append(MCPClient(config.name, config.transport, config.command or None, config.url,
            env=secrets(config.env_from), headers=secrets(config.headers_from), allowed_agents=config.allowed_agents,
            read_only_tools=config.read_only_tools, allowed_tools=config.allowed_tools,
            request_timeout=config.timeout, concurrency=config.concurrency))
        if config.transport == 'stdio':
            from app.services.connections import pinned_command
            clients[-1].command = pinned_command(config.command)
        clients[-1].allowed_users = frozenset({1})
    return clients
