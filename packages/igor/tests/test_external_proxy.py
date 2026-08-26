"""ExternalAgentProxy tool_result normalization.

The peer (Forge) emits tool_result payloads in the Anthropic-native shape
({tool_use_id, is_error, content}), while every consumer — both clients' live
renderers and the turn runner that persists the turn for history — reads the
orchestrator's shape ({id, result}). These tests pin the bridge so a proxied
tool result renders and saves exactly like an in-process one.
"""

from app.core.external_proxy import (
    _RESULT_PREVIEW_CHARS,
    _normalize_tool_result,
    _stringify_content,
)


def test_peer_shape_is_mapped_to_canonical_keys():
    out = _normalize_tool_result(
        {"tool_use_id": "call_1", "is_error": False, "content": "hello world"}
    )
    assert out == {"id": "call_1", "result": "hello world"}


def test_content_block_list_is_flattened_to_text():
    out = _normalize_tool_result(
        {
            "tool_use_id": "call_2",
            "content": [
                {"type": "text", "text": "line one"},
                {"type": "text", "text": "line two"},
            ],
        }
    )
    assert out == {"id": "call_2", "result": "line one\nline two"}


def test_error_result_still_carries_its_text():
    out = _normalize_tool_result(
        {"tool_use_id": "call_3", "is_error": True, "content": "boom: nonzero exit"}
    )
    assert out["id"] == "call_3"
    assert out["result"] == "boom: nonzero exit"


def test_result_is_truncated_to_the_preview_cap():
    out = _normalize_tool_result(
        {"tool_use_id": "call_4", "content": "x" * (_RESULT_PREVIEW_CHARS + 500)}
    )
    assert len(out["result"]) == _RESULT_PREVIEW_CHARS


def test_already_canonical_payload_passes_through():
    # No-op if the peer is ever updated to emit {id, result} directly.
    out = _normalize_tool_result({"id": "call_5", "result": "done"})
    assert out == {"id": "call_5", "result": "done"}


def test_missing_id_yields_none_not_crash():
    out = _normalize_tool_result({"content": "orphan output"})
    assert out == {"id": None, "result": "orphan output"}


def test_non_dict_payload_is_stringified():
    assert _normalize_tool_result("raw string") == {"id": None, "result": "raw string"}


def test_stringify_handles_none_string_and_blocks():
    assert _stringify_content(None) == ""
    assert _stringify_content("plain") == "plain"
    assert _stringify_content([{"type": "text", "text": "a"}, {"text": "b"}]) == "a\nb"


# ── Token usage folded onto DONE (external agents' spend reaches the readout) ──
#
# An in-process turn's DONE carries {"usage": {input, output}} and the UI folds
# that into its header. The peer streams `usage` frames instead; the proxy keeps
# the latest and puts it on DONE so Optimus/Centurion report tokens the same way.

import asyncio

import pytest

from app.core.external_proxy import ExternalAgentProxy
from app.core.peer_routing import PeerInfo
from app.schemas.sse import SSEEventType


class _FakeWs:
    def __init__(self):
        self.sent = []

    def peers(self, agent_id):
        return [PeerInfo(agent_id=agent_id, host="server", platform="linux", roots=[])]

    def is_connected(self, agent_id):
        return True

    async def send(self, agent_id, frame, host=None):
        self.sent.append((agent_id, frame, host))


class _Ctx:
    def __init__(self):
        self.agent_id = "optimus"
        self.session_id = 1
        self.request_id = "req-1"
        self.conversation_history = []
        self.user_id = 1
        self.db = None
        self.extra = {}


async def _drive(events):
    """Run one proxied turn, feeding `events` into its queue in order, and
    collect the SSEEvents it yields."""
    proxy = ExternalAgentProxy(_FakeWs())
    ctx = _Ctx()
    out = []
    gen = proxy.run(ctx)

    async def feed():
        # Wait for the stream to register its queue (the START yield), then push.
        for _ in range(200):
            if proxy._pending:
                break
            await asyncio.sleep(0)
        chat_id = next(iter(proxy._pending))
        for ev in events:
            proxy.deliver(chat_id, ev)

    task = asyncio.create_task(feed())
    async for sse in gen:
        out.append(sse)
    await task
    return out


def test_usage_frame_is_folded_into_done_not_yielded():
    events = [
        {"type": "chunk", "data": "hello"},
        {"type": "usage", "data": {"input": 10, "output": 2, "turns": 1}},
        {"type": "usage", "data": {"input": 30, "output": 8, "turns": 2}},  # cumulative
        {"type": "done", "data": "final text ignored"},
    ]
    out = asyncio.run(_drive(events))

    # No usage event leaks through as its own SSE.
    assert all(e.type != "usage" for e in out)
    done = [e for e in out if e.type == SSEEventType.DONE]
    assert len(done) == 1
    # The LAST (cumulative) snapshot rides on DONE, in the UI's shape.
    assert done[0].data == {"usage": {"input": 30, "output": 8}}


def test_done_without_any_usage_is_an_empty_dict():
    events = [{"type": "chunk", "data": "hi"}, {"type": "done", "data": "text"}]
    out = asyncio.run(_drive(events))
    done = [e for e in out if e.type == SSEEventType.DONE]
    assert len(done) == 1
    assert done[0].data == {}


# ── Steering: inject a mid-turn message into a running external turn ──────────

def test_steer_sends_a_chat_steer_to_the_running_turns_peer():
    async def go():
        ws = _FakeWs()
        proxy = ExternalAgentProxy(ws)
        ctx = _Ctx()
        gen = proxy.run(ctx)

        # Start the turn so its chat_id/request_id are registered, then steer.
        steered = {}
        async def feed():
            for _ in range(200):
                if proxy._by_request:
                    break
                await asyncio.sleep(0)
            steered["ok"] = await proxy.steer(ctx.request_id, "also update the README")
            chat_id = next(iter(proxy._pending))
            proxy.deliver(chat_id, {"type": "done", "data": "x"})

        task = asyncio.create_task(feed())
        async for _ in gen:
            pass
        await task

        # The steer went out as a chat_steer frame to the peer, carrying the text.
        frames = [f for (_a, f, _h) in ws.sent if f.get("type") == "chat_steer"]
        assert len(frames) == 1
        assert frames[0]["text"] == "also update the README"
        assert steered["ok"] is True
    asyncio.run(go())


def test_steer_for_an_unknown_request_is_false():
    async def go():
        proxy = ExternalAgentProxy(_FakeWs())
        assert await proxy.steer("no-such-request", "hi") is False
    asyncio.run(go())


def test_blank_steer_is_false():
    async def go():
        proxy = ExternalAgentProxy(_FakeWs())
        proxy._by_request["r1"] = ("c1", "optimus", "server")
        assert await proxy.steer("r1", "   ") is False
    asyncio.run(go())
