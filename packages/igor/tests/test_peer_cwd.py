"""Work must run on the machine the owner meant.

The regression, 2026-08-04: the owner picked a folder in the desktop file
dialog and asked Optimus to build a multipage site "here". The literal string
`C:\\Users\\AREL TARIM\\Downloads\\Yeni klasör` was forwarded to Optimus, which
runs server-side under systemd at /opt/forge-mk1. On Linux both `\\` and `:`
are legal filename characters, so nothing failed: mkdir created a single
directory whose NAME was the entire Windows path, fifteen files were written
into it, every internal link resolved, and Optimus reported success. The site
was perfect and on the wrong machine.

The first fix refused every Windows path. That held while every peer was Linux
and became wrong the moment one runs on the owner's PC — there `C:\\Users\\…`
is the right answer. So the question is no longer "is this POSIX" but "which
attached machine claims this path", and the property under test is the one that
survives both eras: work never silently runs somewhere other than where it was
aimed.

Optimus stays ONE agent throughout. Its memory, sessions and profile live in
the backend; a host is a transport detail. These tests exist partly to keep it
that way — see test_house_party_sees_one_optimus_per_machine_count.
"""

import pytest
from pydantic import ValidationError

from app.core.context import AgentContext
from app.core.peer_routing import PeerInfo, resolve, well_formed
from app.schemas.agent import AgentRegistration
from app.schemas.chat import ChatRequest
from app.skills.dispatch import DispatchAgentSkill
from app.websocket.manager import WebSocketManager

WINDOWS_PATHS = [
    r"C:\Users\AREL TARIM\Downloads\Yeni klasör",  # the exact prod value
    r"C:\Users\me\project",
    r"D:/work/site",
    r"\\fileserver\share\www",
]

POSIX_PATHS = [
    "/opt/forge-mk1/workspaces/my-site",
    "/home/deploy/project",
    "/tmp/scratch",
]

SERVER = PeerInfo(agent_id="optimus", host="server", platform="linux")
PC = PeerInfo(
    agent_id="optimus", host="arel-pc", platform="windows",
    roots=(r"C:\Users\AREL TARIM\repos",),
)


# ── The original incident, still refused ─────────────────────────────────────


@pytest.mark.parametrize("path", WINDOWS_PATHS)
def test_a_windows_path_never_reaches_a_linux_only_deployment(path):
    """Today's production: one server peer, no PC attached. This is the exact
    case that built a site inside a directory named `C:\\Users\\…`."""
    assert not resolve([SERVER], path).ok


@pytest.mark.parametrize("path", POSIX_PATHS)
def test_a_server_path_routes_to_the_server(path):
    decision = resolve([SERVER], path)
    assert decision.ok and decision.host == "server"


# ── The new half: a peer on the owner's machine ──────────────────────────────


def test_the_owners_path_routes_to_the_owners_pc():
    """The folder identifies the machine — there is no mode to remember."""
    decision = resolve([SERVER, PC], r"C:\Users\AREL TARIM\repos\speda-mark6")
    assert decision.ok and decision.host == "arel-pc"


def test_a_server_path_still_routes_to_the_server_with_a_pc_attached():
    decision = resolve([SERVER, PC], "/opt/forge-mk1/workspaces/x")
    assert decision.ok and decision.host == "server"


def test_a_path_outside_the_pcs_roots_is_refused_not_rerouted():
    """The allowlist is the point. `C:\\Windows` is well-formed for the PC and
    still must not run there, and it must NOT fall through to the server."""
    decision = resolve([SERVER, PC], r"C:\Windows\System32")
    assert not decision.ok
    assert "arel-pc" in decision.error   # names what IS connected


def test_a_refusal_names_the_connected_peers():
    error = resolve([SERVER, PC], r"E:\nowhere\at\all").error
    assert "server" in error and "arel-pc" in error


def test_no_directory_prefers_the_always_on_peer():
    """A PC that may be asleep must never be the default for work that did not
    ask for it."""
    assert resolve([PC, SERVER], None).host == "server"


def test_nothing_connected_is_refused_immediately():
    assert not resolve([], "/anywhere").ok


# ── Path well-formedness, per platform ───────────────────────────────────────


@pytest.mark.parametrize("path", POSIX_PATHS)
def test_posix_paths_are_well_formed_for_linux_only(path):
    assert well_formed(path, "linux")
    assert not well_formed(path, "windows")


@pytest.mark.parametrize("path", WINDOWS_PATHS)
def test_windows_paths_are_well_formed_for_windows_only(path):
    assert well_formed(path, "windows")
    assert not well_formed(path, "linux"), (
        "a Windows path that passes a POSIX check becomes a DIRECTORY NAMED "
        "after the path — the 2026-08-04 failure"
    )


@pytest.mark.parametrize("path", ["relative/path", "", "   ", "./x"])
def test_a_relative_path_is_well_formed_for_neither(path):
    assert not well_formed(path, "linux")
    assert not well_formed(path, "windows")


# ── One agent, several machines ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_second_machine_does_not_evict_the_first():
    """`_connections[agent_id] = websocket` used to mean the last peer to
    connect silently won, so a laptop waking took the server's turns."""
    ws = WebSocketManager()
    await ws.connect("optimus", object(), host="server")
    await ws.connect("optimus", object(), host="arel-pc", platform="windows")

    assert sorted(ws.hosts("optimus")) == ["arel-pc", "server"]
    assert ws.is_connected("optimus", "server")
    assert ws.is_connected("optimus", "arel-pc")


@pytest.mark.asyncio
async def test_one_machine_leaving_leaves_the_other_attached():
    ws = WebSocketManager()
    await ws.connect("optimus", object(), host="server")
    await ws.connect("optimus", object(), host="arel-pc", platform="windows")

    await ws.disconnect("optimus", "arel-pc")

    assert ws.is_connected("optimus")
    assert ws.hosts("optimus") == ["server"]


@pytest.mark.asyncio
async def test_the_last_machine_leaving_takes_the_agent_offline():
    ws = WebSocketManager()
    await ws.connect("optimus", object(), host="server")
    await ws.disconnect("optimus", "server")
    assert not ws.is_connected("optimus")


@pytest.mark.asyncio
async def test_house_party_sees_one_optimus_per_machine_count():
    """The invariant that keeps 'one Optimus' true. A broadcast must reach the
    agent ONCE however many machines it is attached from — two replies from
    one agent is exactly the second-Optimus outcome this design refuses."""
    ws = WebSocketManager()
    await ws.connect("optimus", object(), host="server")
    await ws.connect("optimus", object(), host="arel-pc", platform="windows")
    await ws.connect("optimus", object(), host="laptop-2", platform="windows")

    assert ws.connected_agents() == ["optimus"]


# ── Registration stays backward-compatible ───────────────────────────────────


def test_a_peer_that_sends_no_host_fields_still_registers():
    """The deployed server peer predates all three fields. It must keep working
    without being redeployed first."""
    reg = AgentRegistration(
        agent_id="optimus", agent_name="Optimus", domain="coding",
        capabilities=["run_command"],
    )
    assert reg.host == "default"
    assert reg.platform == "linux"
    assert reg.roots == []


def test_a_peer_with_no_roots_accepts_any_path_for_its_platform():
    """Which is what preserves today's behaviour exactly."""
    bare = PeerInfo(agent_id="optimus", host="default", platform="linux")
    assert resolve([bare], "/opt/anything").ok
    assert not resolve([bare], r"C:\anything").ok


# ── The schema no longer decides ─────────────────────────────────────────────


@pytest.mark.parametrize("path", WINDOWS_PATHS + POSIX_PATHS)
def test_the_schema_passes_paths_through_untouched(path):
    """A Pydantic model cannot see which machines are attached, so it must not
    be the thing that rules a path in or out. Routing does that."""
    assert ChatRequest(message="build me a site", cwd=path).cwd == path


@pytest.mark.parametrize("value", [None, "", "   "])
def test_an_unset_cwd_stays_unset(value):
    """No directory = the default peer's own workspace. Still legal."""
    assert ChatRequest(message="hi", cwd=value).cwd is None


def test_the_schema_still_rejects_a_non_string():
    with pytest.raises(ValidationError):
        ChatRequest(message="hi", cwd=["/a", "/b"])


# ── dispatch_agent(working_directory=…) — the path the MODEL fills in ────────


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
async def test_the_skill_forwards_the_directory_for_routing_to_judge():
    """The skill no longer pre-judges the platform: with a PC peer attached a
    Windows path is correct, and only the dispatcher knows what is attached."""
    spy = _SpyDispatcher()
    skill = DispatchAgentSkill(spy, [("optimus", "coding")])

    await skill.execute(
        {"agent": "optimus", "task": "build a site",
         "working_directory": r"C:\Users\AREL TARIM\repos\site"},
        _context(),
    )

    assert len(spy.calls) == 1
    assert spy.calls[0]["cwd"] == r"C:\Users\AREL TARIM\repos\site"


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
