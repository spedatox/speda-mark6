"""Official SDK transport, bounded discovery and one owner for connection lifetime.

No automatic effect retry, cancellation suppression, paid sampling callbacks or
implicit filesystem roots. Server annotations are data, never grants.
"""
import asyncio
from contextlib import AsyncExitStack
import hashlib
import json
import logging
import os
import re
import time
from urllib.parse import urlsplit
from mcp import Client, StdioServerParameters
from app.schemas.llm import ProviderError

logger = logging.getLogger(__name__)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class MCPClient:
    def __init__(self, server_name, transport="stdio", command=None, url=None, env=None,
                 headers=None, *, allowed_agents=(), read_only_tools=(), allowed_tools=None,
                 request_timeout=30, connect_timeout=15, concurrency=4, max_tools=1000,
                 max_result_bytes=64000, target=None, auth=None):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", server_name):
            raise ValueError("Invalid MCP server identity")
        if transport not in {"stdio", "http"}:
            raise ValueError("Use stdio or Streamable HTTP")
        if url:
            parsed = urlsplit(url)
            if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
                raise ValueError("Use an operator-configured HTTP(S) endpoint without URL credentials")
            if (headers or auth) and parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
                raise ValueError("MCP credentials require TLS outside loopback")
        self.server_name, self.transport, self.command, self.url = server_name, transport, command, url
        self.env, self.headers, self.auth = env or {}, headers or {}, auth
        self.allowed_agents, self.read_only_tools = frozenset(allowed_agents), frozenset(read_only_tools)
        self.allowed_tools = None if allowed_tools is None else frozenset(allowed_tools)
        self.request_timeout, self.connect_timeout = request_timeout, connect_timeout
        self.concurrency = concurrency
        self.max_tools, self.max_result_bytes, self.target = max_tools, max_result_bytes, target
        self._tools, self._connected, self._client, self._task = [], False, None, None
        self._ready, self._stop = asyncio.Event(), asyncio.Event()
        self._slots, self._refresh_lock = asyncio.Semaphore(concurrency), asyncio.Lock()
        self._error, self._catalog_at, self.protocol_version = None, 0, None
        self.generation, self.on_catalog_changed = 0, None
        self.calls, self.failures, self.inflight = 0, 0, 0
        self._admission_epoch = 0

    def ensure_admitted(self, epoch):
        if not self._connected or not getattr(self, 'active', True) or epoch != self._admission_epoch:
            raise ProviderError('mcp_revoked', 'Connection was disabled or replaced before dispatch')

    async def connect(self):
        if self._connected:
            return
        if self._task is None or self._task.done():
            self._ready, self._stop, self._error = asyncio.Event(), asyncio.Event(), None
            self._task = asyncio.create_task(self._lifetime(), name=f"mcp:{self.server_name}")
        try:
            async with asyncio.timeout(self.connect_timeout):
                await self._ready.wait()
        except BaseException:
            await self.disconnect()
            raise
        if self._error:
            raise ProviderError("mcp_connection", f"MCP server {self.server_name} could not connect") from self._error

    async def _lifetime(self):
        try:
            async with AsyncExitStack() as stack:
                target = self.target
                if target is None and self.transport == "http":
                    import httpx2
                    from mcp.client.streamable_http import streamable_http_client
                    http = await stack.enter_async_context(httpx2.AsyncClient(headers=self.headers, auth=self.auth,
                        timeout=self.request_timeout, follow_redirects=False, trust_env=False))
                    target = streamable_http_client(self.url, http_client=http, max_sse_event_size=1024 * 1024)
                elif target is None:
                    if not self.command:
                        raise ValueError("stdio requires an explicit command")
                    environment = {k: v for k, v in os.environ.items() if k.upper() in {
                        "PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"}}
                    environment.update(self.env)
                    target = StdioServerParameters(command=self.command[0], args=self.command[1:], env=environment)
                client = await stack.enter_async_context(Client(target, mode="auto", cache=None,
                    read_timeout_seconds=self.request_timeout, input_required_max_rounds=1))
                self._client, self._connected, self.protocol_version = client, True, client.protocol_version
                await self.refresh_tools()
                self._ready.set()
                await self._stop.wait()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._error = exc
            logger.warning("mcp_connection_failed", extra={"server": self.server_name, "error_type": type(exc).__name__})
        finally:
            self._connected, self._client = False, None
            self._ready.set()

    async def refresh_tools(self):
        async with self._refresh_lock:
            if not self._connected:
                raise ProviderError("mcp_disconnected", "MCP connection is unavailable")
            tools, cursor, seen, names, size = [], None, set(), set(), 0
            async with asyncio.timeout(self.request_timeout):
                for _ in range(100):
                    page = await self._client.list_tools(cursor=cursor, cache_mode="refresh")
                    for tool in page.tools:
                        if tool.name in names:
                            raise ProviderError("mcp_catalog", "Duplicate tool identity from server")
                        names.add(tool.name)
                        if self.allowed_tools is not None and tool.name not in self.allowed_tools:
                            continue
                        definition = {"name": tool.name, "description": tool.description or "",
                            "input_schema": tool.input_schema,
                            "annotations": tool.annotations.model_dump(by_alias=True, exclude_none=True) if tool.annotations else {},
                            "output_schema": tool.output_schema}
                        size += len(json.dumps(definition).encode())
                        if len(tools) >= self.max_tools or size > 2 * 1024**2:
                            raise ProviderError("mcp_catalog_limit", "MCP catalogue exceeds configured limits")
                        from jsonschema import Draft202012Validator
                        Draft202012Validator.check_schema(tool.input_schema)
                        tools.append(definition)
                    cursor = page.next_cursor
                    if not cursor:
                        break
                    if cursor in seen:
                        raise ProviderError("mcp_cursor", "MCP server repeated a catalogue cursor")
                    seen.add(cursor)
                else:
                    raise ProviderError("mcp_catalog_limit", "MCP catalogue page limit reached")
            if fingerprint(tools) != fingerprint(self._tools):
                self.generation += 1
                self._tools = tools
                if self.on_catalog_changed:
                    self.on_catalog_changed(self)
            self._catalog_at = time.monotonic()
            return list(self._tools)

    async def list_tools(self):
        return list(self._tools)

    def read_only(self, name):
        return name in self.read_only_tools

    async def call_tool(self, tool_name, args, *, expected_schema=None, progress=None):
        epoch = self._admission_epoch
        self.ensure_admitted(epoch)
        if not self._connected:
            raise ProviderError("mcp_disconnected", f"MCP server {self.server_name} is disconnected")
        async with asyncio.timeout(self.request_timeout), self._slots:
            self.ensure_admitted(epoch)
            if time.monotonic() - self._catalog_at > 30:
                await self.refresh_tools()
            self.ensure_admitted(epoch)
            definition = next((d for d in self._tools if d['name'] == tool_name), None)
            if definition is None or (expected_schema and fingerprint(definition) != expected_schema):
                raise ProviderError("mcp_schema_changed", "MCP tool changed; rediscover it before executing")
            from jsonschema import Draft202012Validator
            if any(Draft202012Validator(definition['input_schema']).iter_errors(args)):
                raise ProviderError("invalid_arguments", "MCP arguments do not match the authorized schema")
            self.calls += 1
            self.inflight += 1
            try:
                # The SDK's high-level call_tool automatically re-lists and
                # resends HEADER_MISMATCH, and drives embedded input requests.
                # The host must review those changes before another effect.
                result = await self._client.session.call_tool(tool_name, args, read_timeout_seconds=self.request_timeout,
                                                       progress_callback=progress)
                return self.encode(result)
            except asyncio.CancelledError:
                self.failures += 1
                raise
            except Exception as exc:
                self.failures += 1
                raise ProviderError("mcp_outcome_unknown", "MCP call did not return a confirmed result; do not replay automatically") from exc
            finally:
                self.inflight -= 1

    def encode(self, result):
        value = result.model_dump(mode="json", by_alias=True, exclude_none=True)
        encoded = json.dumps(value, ensure_ascii=False)
        if len(encoded.encode()) <= self.max_result_bytes:
            return encoded
        preview = "\n".join(getattr(c, "text", "") for c in getattr(result, "content", ()))
        return json.dumps({"isError": value.get("isError", False), "truncated": True,
            "original_sha256": fingerprint(value), "original_bytes": len(encoded.encode()),
            "content": [{"type": "text", "text": preview.encode()[:self.max_result_bytes // 2].decode('utf-8', errors='ignore')}],
            "notice": "Result exceeds the context limit; request a narrower result. Binary blocks omitted."}, ensure_ascii=False)

    async def request(self, method, **arguments):
        epoch = self._admission_epoch
        self.ensure_admitted(epoch)
        if method not in {"list_resources", "list_resource_templates", "read_resource", "list_prompts", "get_prompt", "complete"}:
            raise ValueError("Unsupported MCP data operation")
        if not self._connected:
            raise ProviderError("mcp_disconnected", "MCP connection is unavailable")
        async with asyncio.timeout(self.request_timeout), self._slots:
            self.ensure_admitted(epoch)
            return self.encode(await getattr(self._client.session, method)(**arguments))

    def status(self):
        return {"server": self.server_name, "connected": self._connected, "protocol_version": self.protocol_version,
            "generation": self.generation, "tools": len(self._tools), "calls": self.calls,
            "failures": self.failures, "inflight": self.inflight}

    async def disconnect(self):
        self._admission_epoch += 1
        self._connected = False
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._task), 5)
            except TimeoutError:
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)
        self._connected = False
