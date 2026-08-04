"""
A working directory sent to the Forge peer must be a SERVER path.

The regression, 2026-08-04: the owner picked a folder in the desktop file
dialog and asked Optimus to build a multipage site "here". The literal string
`C:\\Users\\AREL TARIM\\Downloads\\Yeni klasör` was forwarded to Optimus, which
runs server-side under systemd at /opt/forge-mk1. On Linux both `\\` and `:`
are legal filename characters, so nothing failed: mkdir created a single
directory whose NAME was the entire Windows path, fifteen files were written
into it, every internal link resolved, and Optimus reported success. The site
was perfect and on the wrong machine.

Silent success in the wrong place is worse than a loud failure, which is why
both entry points now refuse instead of forwarding.
"""

import pytest
from pydantic import ValidationError

from app.core.context import AgentContext
from app.schemas.chat import ChatRequest
from app.skills.dispatch import DispatchAgentSkill

WINDOWS_PATHS = [
    r"C:\Users\AREL TARIM\Downloads\Yeni klasör",  # the exact prod value
    r"C:\Users\me\project",
    r"D:/work/site",
    r"\\fileserver\share\www",
    r"some\relative\windows\path",
]

POSIX_PATHS = [
    "/opt/forge-mk1/workspaces/my-site",
    "/home/deploy/project",
    "/tmp/scratch",
]


# ── ChatRequest.cwd — the direct /chat/optimus path the owner actually used ──


@pytest.mark.parametrize("path", WINDOWS_PATHS)
def test_chat_request_refuses_a_windows_cwd(path):
    with pytest.raises(ValidationError) as exc:
        ChatRequest(message="build me a site", cwd=path)
    assert "Windows path" in str(exc.value)


@pytest.mark.parametrize("path", POSIX_PATHS)
def test_chat_request_accepts_a_posix_cwd(path):
    assert ChatRequest(message="build me a site", cwd=path).cwd == path


@pytest.mark.parametrize("value", [None, "", "   "])
def test_an_unset_cwd_stays_unset(value):
    """No directory = the peer's own workspace. That must remain legal."""
    assert ChatRequest(message="hi", cwd=value).cwd is None


# ── dispatch_agent(working_directory=…) — the path the MODEL fills in ───────


def _context() -> AgentContext:
    return AgentContext(
        agent_id="speda", user_id=1, session_id=1, request_id="req",
        triggered_by="user", trigger_payload={}, output_mode="respond",
        model="m", system_prompt="", conversation_history=[], db=None,
        timezone="Europe/Istanbul",
    )


class _SpyDispatcher:
    """Records whether the dispatch was actually allowed through."""

    def __init__(self):
        self.calls = []

    async def dispatch(self, **kw):
        self.calls.append(kw)
        return "done"

    async def spawn(self, **kw):
        self.calls.append(kw)
        return "ticket"

    async def broadcast(self, **kw):
        self.calls.append(kw)
        return "broadcast"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", WINDOWS_PATHS)
async def test_dispatch_refuses_a_windows_working_directory(path):
    spy = _SpyDispatcher()
    skill = DispatchAgentSkill(spy, [("optimus", "coding")])

    result = await skill.execute(
        {"agent": "optimus", "task": "build a site", "working_directory": path},
        _context(),
    )

    assert "Refused" in result
    assert spy.calls == [], "the dispatch was forwarded to the peer anyway"


@pytest.mark.asyncio
async def test_dispatch_allows_a_posix_working_directory():
    spy = _SpyDispatcher()
    skill = DispatchAgentSkill(spy, [("optimus", "coding")])

    await skill.execute(
        {"agent": "optimus", "task": "build a site",
         "working_directory": "/opt/forge-mk1/workspaces/site"},
        _context(),
    )

    assert len(spy.calls) == 1
    assert spy.calls[0]["cwd"] == "/opt/forge-mk1/workspaces/site"


@pytest.mark.asyncio
async def test_dispatch_without_a_directory_is_unaffected():
    spy = _SpyDispatcher()
    skill = DispatchAgentSkill(spy, [("sentinel", "finance")])

    await skill.execute({"agent": "sentinel", "task": "model the budget"}, _context())

    assert len(spy.calls) == 1
    assert spy.calls[0]["cwd"] is None
