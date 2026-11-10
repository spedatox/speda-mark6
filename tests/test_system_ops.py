import json

import httpx
import pytest

from app.core.registry import CapabilityRegistry
from app.profiles.registry import ProfileRegistry
from app.schemas.llm import ProviderError
from app.skills.system_ops import SystemOpsSkill
from tests.conftest import context


async def test_system_ops_is_discoverable_only_for_owner_orion_and_returns_selected_services(monkeypatch):
    def docker(request):
        assert request.method == "GET" and request.url.host == "docker"
        assert request.url.path == "/containers/json"
        return httpx.Response(200, json=[
            {"Names": ["/speda-renovated-app-1"], "State": "running", "Status": "Up (healthy)",
             "Labels": {"private": "must not leave the daemon"}},
            {"Names": ["/speda-pg-restore-123456789012"], "State": "exited", "Status": "Exited (137)"},
            {"Names": ["/unrelated"], "State": "running", "Status": "Up"},
        ])

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kwargs: httpx.MockTransport(docker))
    registry = CapabilityRegistry()
    await registry.register_skill(SystemOpsSkill(None))
    orion = context(agent_id="orion")
    orion.allowed_tools = ProfileRegistry().require("orion").tools
    assert "system_ops" in registry.discovery_index(orion)
    result = json.loads(await registry.execute("system_ops", {"action": "services"}, orion))
    assert result["services"] == [{"name": "speda-renovated-app-1", "state": "running", "status": "Up (healthy)"}]

    for other in (context(agent_id="optimus"), context(user_id=2, agent_id="orion"),
                  context(agent_id="orion", task_role="worker")):
        assert "system_ops" not in registry.discovery_index(other)
        with pytest.raises(ProviderError, match="not authorized"):
            await registry.execute("system_ops", {}, other)

async def test_orion_command_uses_host_transport_and_a_stable_receipt(monkeypatch):
    from hashlib import sha256
    from types import SimpleNamespace

    from app.services import host_operations

    requests = []

    async def connect(path, *, limit):
        assert path == host_operations.SOCKET_PATH
        assert limit == 128 * 1024

        class Writer:
            def write(self, data):
                requests.append(json.loads(data))

            async def drain(self):
                pass

            def close(self):
                pass

        async def readline():
            return b'{"status":"ok","exit_code":0,"stdout":"host output"}\n'

        return SimpleNamespace(readline=readline), Writer()

    monkeypatch.setattr(host_operations.asyncio, "open_unix_connection", connect, raising=False)
    registry = CapabilityRegistry()
    await registry.register_skill(SystemOpsSkill(None))
    orion = context(agent_id="orion")
    orion.allowed_tools = ProfileRegistry().require("orion").tools
    orion.tool_call_id = "fixture-call"
    command = {"action": "exec", "command": "uname -s"}
    reply = json.loads(await registry.execute("system_ops", command, orion))
    expected = sha256(f"{orion.request_id}:fixture-call".encode()).hexdigest()[:32]
    assert requests == [{**command, "operation_id": expected}]
    assert reply == {"status": "ok", "exit_code": 0, "stdout": "host output",
                     "source": "host", "operation_id": expected}
    assert not registry.call_is_read_only("system_ops", command)
