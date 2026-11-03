"""Inherited vendor REST capabilities behind the registry's shared admission controls.

This is a REST backend, not an MCP wire transport. It shares bounded discovery,
grants, schema pins and effect receipts with MCP tools without a second registry.
"""
import asyncio
import json
from jsonschema import Draft202012Validator
from app.mcp.client import MCPClient, fingerprint
from app.schemas.llm import ProviderError

READ_ONLY_TOOLS = frozenset({
    'gmail_search', 'gmail_read', 'calendar_list_calendars', 'calendar_list_events',
    'calendar_get_event', 'calendar_list_instances', 'calendar_freebusy',
    'tasks_list_lists', 'tasks_list', 'drive_search', 'drive_read', 'contacts_search',
    'outlook_search', 'outlook_read', 'outlook_list_folders',
})


class RestCapabilityClient(MCPClient):
    def __init__(self, name, tools, dispatch, token_provider):
        super().__init__(name, transport="http", request_timeout=30, concurrency=4)
        self._dispatch, self.token_provider = dispatch, token_provider
        self.allowed_users = frozenset({1})  # Existing vendor accounts belong to the configured owner.
        names = set()
        for tool in tools:
            if tool["name"] in names:
                raise ValueError("Duplicate REST capability identity")
            names.add(tool["name"])
            Draft202012Validator.check_schema(tool["input_schema"])
        self._tools = list(tools)
        self.read_only_tools = READ_ONLY_TOOLS.intersection(names)

    async def connect(self):
        async with asyncio.timeout(self.connect_timeout):
            token = await self.token_provider.get()
        if not token:
            raise ProviderError("not_connected", "Vendor account needs authentication")
        self._connected = True
        self.generation += 1

    async def refresh_catalog(self):
        # The dispatch catalogue is versioned local code, not a remote tool listing.
        return list(self._tools)

    async def refresh_tools(self):
        return await self.refresh_catalog()

    async def call_tool(self, name, args, *, expected_schema=None, progress=None):
        epoch = self._admission_epoch
        self.ensure_admitted(epoch)
        if not self._connected:
            raise ProviderError("not_connected", "Vendor account is disconnected")
        definition = next((tool for tool in self._tools if tool["name"] == name), None)
        if not definition or (expected_schema and expected_schema != fingerprint(definition)):
            raise ProviderError("mcp_schema_changed", "Capability changed; start a new turn")
        if not Draft202012Validator(definition["input_schema"]).is_valid(args):
            raise ProviderError("invalid_arguments", "Arguments do not match the authorized schema")
        async with asyncio.timeout(self.request_timeout), self._slots:
            self.ensure_admitted(epoch)
            token = await self.token_provider.get()
            self.ensure_admitted(epoch)
            if not token:
                self._connected = False
                raise ProviderError("not_connected", "Vendor account needs authentication")
            self.calls += 1
            self.inflight += 1
            try:
                result = await self._dispatch(name, args, token)
                raw = result.encode("utf-8")
                if len(raw) > self.max_result_bytes:
                    return json.dumps({"truncated":True, "original_bytes":len(raw),
                        "original_sha256":fingerprint(result), "text":raw[:self.max_result_bytes // 2].decode("utf-8", errors="ignore")})
                return result
            except asyncio.CancelledError:
                self.failures += 1
                raise
            except Exception as exc:
                self.failures += 1
                raise ProviderError("vendor_outcome_unknown", "Vendor call did not return a confirmed result; inspect before retrying") from exc
            finally:
                self.inflight -= 1

    async def request(self, method, **arguments):
        raise ProviderError("unsupported_capability", "This REST bridge exposes tools only")

    def status(self):
        return {**super().status(), "backend":"REST"}

    async def disconnect(self):
        self._admission_epoch += 1
        self._connected = False
