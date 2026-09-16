# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""AgentDispatcher.push_to_peers — fire-and-forget delivery to a connected
external peer. Unlike dispatch(), there
is no reply to correlate: this exists to answer "did anything receive this",
not "what did it say back".
"""

import pytest

from app.core.dispatch import AgentDispatcher


class _FakeWsManager:
    def __init__(self, hosts_by_agent=None):
        self._hosts = hosts_by_agent or {}
        self.sent: list[tuple[str, dict, str]] = []

    def hosts(self, agent_id):
        return list(self._hosts.get(agent_id, []))

    async def send(self, agent_id, message, host=None):
        self.sent.append((agent_id, message, host))


@pytest.mark.asyncio
async def test_an_unwired_dispatcher_reaches_nobody():
    """Before wire() runs (Tier-1 registration precedes it) there is no
    WebSocketManager yet — a push in that window must not raise."""
    d = AgentDispatcher()
    reached = await d.push_to_peers("external-agent", {"type": "notice"})
    assert reached == 0


@pytest.mark.asyncio
async def test_no_connected_peer_is_zero_not_an_error():
    ws = _FakeWsManager()
    d = AgentDispatcher()
    d.wire(orchestrator=None, profiles=None, session_manager=None, ws_manager=ws)

    reached = await d.push_to_peers("external-agent", {"type": "notice"})

    assert reached == 0
    assert ws.sent == []


@pytest.mark.asyncio
async def test_the_frame_reaches_every_host_the_agent_is_attached_from():
    """One agent_id, several machines — the same peer on a server and on the
    owner's PC is still one agent (websocket/manager.py), so a push has to
    fan out to all of them, not just the first."""
    ws = _FakeWsManager({"external-agent": ["server", "owners-pc"]})
    d = AgentDispatcher()
    d.wire(orchestrator=None, profiles=None, session_manager=None, ws_manager=ws)

    reached = await d.push_to_peers("external-agent", {"type": "notice", "body": "x"})

    assert reached == 2
    hosts_sent = {host for _, _, host in ws.sent}
    assert hosts_sent == {"server", "owners-pc"}
    assert all(msg == {"type": "notice", "body": "x"} for _, msg, _ in ws.sent)


@pytest.mark.asyncio
async def test_it_only_reaches_the_named_agent():
    ws = _FakeWsManager({"external-agent": ["server"], "other-agent": ["server"]})
    d = AgentDispatcher()
    d.wire(orchestrator=None, profiles=None, session_manager=None, ws_manager=ws)

    await d.push_to_peers("external-agent", {"type": "notice"})

    assert [agent for agent, _, _ in ws.sent] == ["external-agent"]
