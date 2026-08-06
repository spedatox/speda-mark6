"""An external peer runs the owner's turns, so it must know what the owner's
in-process agents know.

`ExternalAgentProxy` sent `"system_prompt": None` — correct, because the peer
owns its own identity and Rule 2 keeps prompt construction here. But nothing
else carried the owner's memory either, so Optimus ran every turn with the
conversation and no idea who it was talking to. Nothing errored; a missing
background fact does not raise. It just produced an agent that had never met
the owner.

The block now rides alongside the history, on the same terms: the DB is the
source of truth and it is re-sent every turn rather than cached on the peer.
"""

import pytest

from app.core.external_proxy import ExternalAgentProxy


class _Ctx:
    """Enough AgentContext for the recall path."""

    def __init__(self, db="db"):
        self.agent_id = "optimus"
        self.user_id = 1
        self.session_id = 7
        self.request_id = "req-1"
        self.db = db
        self.conversation_history = [{"role": "user", "content": "hi"}]
        self.extra = {}


@pytest.mark.asyncio
async def test_the_owners_memory_is_sent_to_the_peer(monkeypatch):
    async def _recall(user_id, db, agent_id, *, cache):
        assert (user_id, agent_id) == (1, "optimus")
        return "## Owner\nAhmet Erol."

    monkeypatch.setattr("app.core.external_proxy.recall_for_context", _recall)
    proxy = ExternalAgentProxy(ws_manager=None, memory_cache=object())

    assert "Ahmet Erol" in await proxy._memory_block(_Ctx())   # noqa: SLF001


@pytest.mark.asyncio
async def test_no_cache_wired_means_no_memory_not_a_crash():
    """Construction order changed to give the proxy the cache; a caller that
    predates that must not take the turn down with it."""
    proxy = ExternalAgentProxy(ws_manager=None)
    assert await proxy._memory_block(_Ctx()) == ""             # noqa: SLF001


@pytest.mark.asyncio
async def test_a_turn_with_no_db_session_asks_for_nothing():
    proxy = ExternalAgentProxy(ws_manager=None, memory_cache=object())
    assert await proxy._memory_block(_Ctx(db=None)) == ""      # noqa: SLF001


@pytest.mark.asyncio
async def test_a_failed_recall_costs_the_memory_not_the_job(monkeypatch):
    """Memory is context, not the turn. A recall that fails should cost the
    peer some background knowledge and nothing else — the owner asked for the
    work, not for the memory."""
    async def _boom(*a, **k):
        raise RuntimeError("memory table is locked")

    monkeypatch.setattr("app.core.external_proxy.recall_for_context", _boom)
    proxy = ExternalAgentProxy(ws_manager=None, memory_cache=object())

    assert await proxy._memory_block(_Ctx()) == ""             # noqa: SLF001


@pytest.mark.asyncio
async def test_a_recall_returning_nothing_is_an_empty_string(monkeypatch):
    """None would be serialized as null and reach the peer as a non-string."""
    async def _none(*a, **k):
        return None

    monkeypatch.setattr("app.core.external_proxy.recall_for_context", _none)
    proxy = ExternalAgentProxy(ws_manager=None, memory_cache=object())

    result = await proxy._memory_block(_Ctx())                 # noqa: SLF001
    assert result == "" and isinstance(result, str)
