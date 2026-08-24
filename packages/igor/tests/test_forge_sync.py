"""sync_owner_memory_to_forge — Orion's push of the composed owner memory out
to a connected Forge peer (Pass 6 of the nightly audit, app/prompts/agents/
orion/02_audit.md). Restricted to Orion; reuses recall_for_context() and
AgentDispatcher.push_to_peers(), covered on their own in
test_peer_memory_channel.py and test_dispatch_push_to_peers.py respectively —
this file is about the skill's own gate and wiring, not those two again.
"""

import pytest

from app.skills.forge_sync import SyncOwnerMemoryToForgeSkill


class _Ctx:
    def __init__(self, agent_id="orion"):
        self.agent_id = agent_id
        self.user_id = 1
        self.db = "db"


class _Dispatcher:
    def __init__(self, reached=0):
        self.reached = reached
        self.calls: list[tuple[str, dict]] = []

    async def push_to_peers(self, agent_id, frame):
        self.calls.append((agent_id, frame))
        return self.reached


@pytest.mark.asyncio
async def test_only_orion_may_call_it(monkeypatch):
    async def _boom(*a, **k):
        raise AssertionError("should never compose memory for a non-Orion caller")

    monkeypatch.setattr("app.skills.forge_sync.recall_for_context", _boom)
    dispatcher = _Dispatcher()
    skill = SyncOwnerMemoryToForgeSkill(dispatcher)

    result = await skill.execute({}, _Ctx(agent_id="speda"))

    assert "restricted to Orion" in result
    assert dispatcher.calls == []


@pytest.mark.asyncio
async def test_it_pushes_the_composed_block_to_the_forge_agent_id(monkeypatch):
    async def _recall(user_id, db, agent_id, *, cache):
        assert (user_id, db, agent_id) == (1, "db", "optimus")
        return "## Owner\nAhmet Erol."

    monkeypatch.setattr("app.skills.forge_sync.recall_for_context", _recall)
    dispatcher = _Dispatcher(reached=1)
    skill = SyncOwnerMemoryToForgeSkill(dispatcher)

    result = await skill.execute({}, _Ctx())

    assert dispatcher.calls == [
        ("optimus", {"type": "owner_memory_sync", "block": "## Owner\nAhmet Erol."})
    ]
    assert "1 connected" in result


@pytest.mark.asyncio
async def test_no_connected_peer_is_reported_plainly_not_as_an_error(monkeypatch):
    async def _recall(*a, **k):
        return "## Owner\n..."

    monkeypatch.setattr("app.skills.forge_sync.recall_for_context", _recall)
    dispatcher = _Dispatcher(reached=0)
    skill = SyncOwnerMemoryToForgeSkill(dispatcher)

    result = await skill.execute({}, _Ctx())

    assert "No Forge/Optimus peer is currently connected" in result
