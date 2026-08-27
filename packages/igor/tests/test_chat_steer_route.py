"""POST /chat/steer/{request_id} — the desktop counterpart of the Telegram
gateway's steering. Tested directly against the route function: it only reads
request.app.state.agent_proxy, so a minimal fake stands in for the real
Request/FastAPI app.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.routers.chat import chat_steer
from app.schemas.chat import SteerRequest


def _request(proxy):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(agent_proxy=proxy)))


class _Proxy:
    def __init__(self, result: bool):
        self.result = result
        self.calls: list[tuple[str, str]] = []

    async def steer(self, request_id: str, text: str) -> bool:
        self.calls.append((request_id, text))
        return self.result


def test_steer_delegates_to_the_proxy_and_reports_success():
    proxy = _Proxy(True)
    out = asyncio.run(chat_steer("req-1", SteerRequest(text="also update the README"),
                                 _request(proxy)))
    assert out == {"steered": True}
    assert proxy.calls == [("req-1", "also update the README")]


def test_steer_reports_false_when_the_proxy_refuses():
    proxy = _Proxy(False)
    out = asyncio.run(chat_steer("req-1", SteerRequest(text="hi"), _request(proxy)))
    assert out == {"steered": False}


def test_steer_is_false_with_no_proxy_wired():
    out = asyncio.run(chat_steer("req-1", SteerRequest(text="hi"), _request(None)))
    assert out == {"steered": False}
