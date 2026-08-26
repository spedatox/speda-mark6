"""The peer's half of the memory tool — the other end of the socket.

`test_peer_memory.py` covers the block Igor SENDS the peer every turn. This is
what happens when the peer acts on it. Since the memory redesign that block says
"here is a directory listing and four preloaded files; use the `memory` tool for
the rest", and Optimus is the one agent that does not run in this process — so
it had no such tool and the instruction named nothing. It could not open a
project file, look up a person, read a ledger, or write down anything it
learned.

The rule the tests below exist to hold: an external agent gets memory through
the SAME skill as an in-process one. Not a subset, not a parallel path. Anything
Sentinel is refused, Optimus is refused, by the same code — otherwise "external"
becomes a way around the file law.
"""

import pytest

from app.services import peer_memory


class _Session:
    """A stand-in for AsyncSessionLocal()'s context manager."""

    async def __aenter__(self):
        return "db"

    async def __aexit__(self, *_exc):
        return False


@pytest.fixture(autouse=True)
def _no_database(monkeypatch):
    monkeypatch.setattr(peer_memory, "AsyncSessionLocal", _Session)


def _skill(monkeypatch, result="done", *, capture=None):
    class _Skill:
        async def execute(self, args, context):
            if capture is not None:
                capture.append((args, context))
            return result

    monkeypatch.setattr(peer_memory, "MemorySkill", _Skill)


# ── The command runs, and the answer comes back ──────────────────────────────

@pytest.mark.asyncio
async def test_a_command_reaches_the_skill_and_its_answer_goes_back(monkeypatch):
    seen: list = []
    _skill(monkeypatch, "# The Forge\nStatus: active", capture=seen)

    response = await peer_memory.run_memory_command("optimus", {
        "type": "memory_request", "request_id": "r1",
        "command": "view", "path": "/memories/projects/forge.md",
    })

    assert response["ok"] is True
    assert response["request_id"] == "r1"
    assert "Status: active" in response["result"]
    assert seen[0][0] == {"command": "view", "path": "/memories/projects/forge.md"}


@pytest.mark.asyncio
async def test_the_peer_is_the_author_of_its_own_writes(monkeypatch):
    """The load-bearing field. The file law records which agent owns which
    document and `check_write` refuses on the author — so Optimus writing the
    finance ledger has to be refused by the same code that would refuse Ultron.
    Passing anything else here would let an external agent write as somebody
    else."""
    seen: list = []
    _skill(monkeypatch, capture=seen)

    await peer_memory.run_memory_command("optimus", {
        "request_id": "r2", "command": "str_replace",
        "path": "/memories/finance.md", "old_str": "a", "new_str": "b",
    })

    context = seen[0][1]
    assert context.agent_id == "optimus"
    assert context.user_id == 1               # single-user system


@pytest.mark.asyncio
async def test_only_the_skills_own_arguments_are_forwarded(monkeypatch):
    """The frame also carries routing fields, which are Igor's business. A peer
    that one day adds a field must not reach the skill's kwargs with it."""
    seen: list = []
    _skill(monkeypatch, capture=seen)

    await peer_memory.run_memory_command("optimus", {
        "type": "memory_request", "request_id": "r3", "chat_id": "c9",
        "command": "insert", "path": "/memories/log.md",
        "insert_line": 3, "insert_text": "note", "surprise": "!!",
    })

    assert seen[0][0] == {"command": "insert", "path": "/memories/log.md",
                          "insert_line": 3, "insert_text": "note"}


# ── ok means "it ran", not "you got what you wanted" ─────────────────────────

@pytest.mark.asyncio
async def test_a_refusal_the_skill_wrote_is_still_a_successful_call(monkeypatch):
    """A path that does not exist, an old_str appearing twice, a document this
    agent does not own — those are real answers, written to be read by a model.
    Reporting them as backend failures would tell the peer to retry the one
    thing that cannot work."""
    _skill(monkeypatch, "Error: The path /memories/nope.md does not exist.")

    response = await peer_memory.run_memory_command("optimus", {
        "request_id": "r4", "command": "view", "path": "/memories/nope.md",
    })

    assert response["ok"] is True
    assert "does not exist" in response["result"]


@pytest.mark.asyncio
async def test_a_command_that_could_not_run_is_a_failure(monkeypatch):
    """The only case where the peer must believe nothing about what happened.
    A write reported as ok when it never ran costs the fact AND the knowledge
    that it was lost."""
    class _Broken:
        async def execute(self, args, context):
            raise RuntimeError("the database is gone")

    monkeypatch.setattr(peer_memory, "MemorySkill", _Broken)

    response = await peer_memory.run_memory_command("optimus", {
        "request_id": "r5", "command": "create",
        "path": "/memories/projects/x.md", "file_text": "...",
    })

    assert response["ok"] is False
    assert "the database is gone" in response["result"]


@pytest.mark.asyncio
async def test_it_never_raises_into_the_socket(monkeypatch):
    """A peer is parked on this inside one tool dispatch. An exception that
    escaped would leave it waiting out its own timeout with nothing to say."""
    class _Broken:
        async def execute(self, args, context):
            raise KeyError("boom")

    monkeypatch.setattr(peer_memory, "MemorySkill", _Broken)

    response = await peer_memory.run_memory_command("optimus", {"command": "view",
                                                                "path": "/x"})
    assert response["type"] == "memory_response"
    assert response["ok"] is False


@pytest.mark.asyncio
async def test_an_unknown_command_is_refused_before_the_database(monkeypatch):
    """Named, so the peer can correct itself rather than guess."""
    def _never(*_a, **_k):
        raise AssertionError("should not have opened a session")

    monkeypatch.setattr(peer_memory, "AsyncSessionLocal", _never)

    response = await peer_memory.run_memory_command("optimus", {
        "request_id": "r6", "command": "rm -rf", "path": "/memories",
    })

    assert response["ok"] is False
    for valid in ("view", "create", "str_replace", "insert", "delete"):
        assert valid in response["result"]


@pytest.mark.asyncio
async def test_every_command_the_skill_implements_is_accepted(monkeypatch):
    """The two lists drifting apart is a whole verb going missing, and it would
    show up as the peer being unable to file something rather than as an
    error anybody sees."""
    from app.skills.memory import MemorySkill

    _skill(monkeypatch)
    declared = set(MemorySkill.input_schema["properties"]["command"]["enum"])

    assert declared == peer_memory._VALID_COMMANDS      # noqa: SLF001

    for command in declared:
        response = await peer_memory.run_memory_command(
            "optimus", {"command": command, "path": "/memories/log.md"})
        assert response["ok"] is True, command


# ── Wiring ───────────────────────────────────────────────────────────────────

def test_the_socket_routes_the_frame_and_does_not_block_on_it():
    """Answered off the receive loop. A database round trip inline would stall
    every other frame behind it — including the chat_event stream of the very
    turn that is waiting for this answer."""
    import inspect

    from app.routers import agents

    src = inspect.getsource(agents.agent_websocket)
    assert 'msg_type == "memory_request"' in src
    assert "asyncio.create_task" in src


def test_the_router_holds_no_logic():
    """CLAUDE.md Rule 1. The helper exists to give create_task something to
    call; every decision is in the service."""
    import inspect

    from app.routers import agents

    body = inspect.getsource(agents._answer_memory)          # noqa: SLF001
    assert "run_memory_command" in body
    # No command handling, no path rules, no session management in the router.
    for leak in ("AsyncSessionLocal", "MemorySkill", "str_replace", "user_id"):
        assert leak not in body, leak


# ── The second skill: record_observation, for the Forge queue flush ─────────

def _observation_skill(monkeypatch, result="Recorded 1 observation(s):\n  [id:9] ...",
                        *, capture=None):
    class _Skill:
        async def execute(self, args, context):
            if capture is not None:
                capture.append((args, context))
            return result

    monkeypatch.setattr(peer_memory, "RecordObservationSkill", _Skill)


@pytest.mark.asyncio
async def test_a_record_observation_frame_reaches_the_skill(monkeypatch):
    seen: list = []
    _observation_skill(monkeypatch, capture=seen)

    response = await peer_memory.run_memory_command("optimus", {
        "request_id": "o1", "skill": "record_observation",
        "content": "Prefers dark mode.", "level": "explicit", "domain": "state",
    })

    assert response["ok"] is True
    assert response["request_id"] == "o1"
    args, context = seen[0]
    assert args == {"observations": [
        {"content": "Prefers dark mode.", "level": "explicit", "domain": "state"},
    ]}
    assert context.agent_id == "optimus"      # same author rule as the memory path


@pytest.mark.asyncio
async def test_the_default_skill_is_still_the_flat_memory_path(monkeypatch):
    """`skill` absent from the frame must not silently change behaviour for
    every peer that predates this feature."""
    seen: list = []
    _skill(monkeypatch, capture=seen)

    await peer_memory.run_memory_command("optimus", {
        "request_id": "o2", "command": "view", "path": "/memories/current.md",
    })

    assert seen[0][0] == {"command": "view", "path": "/memories/current.md"}


@pytest.mark.asyncio
async def test_a_record_observation_frame_missing_required_fields_is_refused(monkeypatch):
    def _never(*_a, **_k):
        raise AssertionError("should not have opened a session")

    monkeypatch.setattr(peer_memory, "AsyncSessionLocal", _never)

    response = await peer_memory.run_memory_command("optimus", {
        "request_id": "o3", "skill": "record_observation", "content": "no domain or level",
    })

    assert response["ok"] is False
    assert "content, level and domain" in response["result"]


@pytest.mark.asyncio
async def test_a_record_observation_that_could_not_run_is_a_failure(monkeypatch):
    class _Broken:
        async def execute(self, args, context):
            raise RuntimeError("the database is gone")

    monkeypatch.setattr(peer_memory, "RecordObservationSkill", _Broken)

    response = await peer_memory.run_memory_command("optimus", {
        "request_id": "o4", "skill": "record_observation",
        "content": "x", "level": "explicit", "domain": "state",
    })

    assert response["ok"] is False
    assert "the database is gone" in response["result"]


def test_the_response_is_the_shape_the_peer_parks_on():
    """request_id correlates the reply to the call; `ok` travels explicitly
    because a successful `delete` says almost nothing and guessing from an
    empty body would report it as broken."""
    import asyncio as _asyncio

    async def scenario(monkeypatch=None):
        return await peer_memory.run_memory_command(
            "optimus", {"request_id": "r7", "command": "nope", "path": "/x"})

    response = _asyncio.run(scenario())
    assert set(response) == {"type", "request_id", "ok", "result"}
    assert response["type"] == "memory_response"


# ── The read skills: recall_conversations and read_agent_channel ─────────────

def _read_skill(monkeypatch, name, result="...", *, capture=None):
    """Swap the class a read route builds, capturing (args, context)."""
    class _Skill:
        async def execute(self, args, context):
            if capture is not None:
                capture.append((args, context))
            return result

    monkeypatch.setitem(
        peer_memory._READ_SKILLS,                                  # noqa: SLF001
        name,
        peer_memory._ReadSkill(                                    # noqa: SLF001
            _Skill,
            keys=peer_memory._READ_SKILLS[name].keys,              # noqa: SLF001
            required=peer_memory._READ_SKILLS[name].required,      # noqa: SLF001
        ),
    )


@pytest.mark.asyncio
async def test_a_recall_frame_reaches_the_semantic_search_skill(monkeypatch):
    seen: list = []
    _read_skill(monkeypatch, "recall_conversations",
                "Found 2 relevant exchange(s)...", capture=seen)

    response = await peer_memory.run_memory_command("optimus", {
        "type": "memory_request", "request_id": "s1",
        "skill": "recall_conversations",
        "query": "the migration we discussed", "after": "2026-06-01", "limit": 5,
    })

    assert response["ok"] is True
    assert response["request_id"] == "s1"
    assert "Found 2 relevant" in response["result"]
    args, context = seen[0]
    assert args == {"query": "the migration we discussed",
                    "after": "2026-06-01", "limit": 5}
    # The load-bearing field on the read path too: recall runs AS the peer, so
    # its agent_id filter and per-user scope are the peer's, not somebody else's.
    assert context.agent_id == "optimus"
    assert context.user_id == 1


@pytest.mark.asyncio
async def test_a_read_agent_channel_frame_reaches_its_skill(monkeypatch):
    """The cross-agent half: Optimus reading the channel is how it learns what
    Centurion has been doing, through the one skill that already renders it."""
    seen: list = []
    _read_skill(monkeypatch, "read_agent_channel",
                "AGENT NETWORK CHANNEL...", capture=seen)

    response = await peer_memory.run_memory_command("centurion", {
        "request_id": "s2", "skill": "read_agent_channel",
        "agent": "optimus", "limit": 10,
    })

    assert response["ok"] is True
    assert seen[0][0] == {"agent": "optimus", "limit": 10}
    assert seen[0][1].agent_id == "centurion"


@pytest.mark.asyncio
async def test_only_the_read_skills_own_arguments_are_forwarded(monkeypatch):
    """Routing fields (request_id, chat_id, type, skill) are Igor's business;
    a peer that adds a field must not reach the skill's kwargs with it."""
    seen: list = []
    _read_skill(monkeypatch, "recall_conversations", capture=seen)

    await peer_memory.run_memory_command("optimus", {
        "type": "memory_request", "request_id": "s3", "chat_id": "c1",
        "skill": "recall_conversations", "query": "x", "surprise": "!!",
    })

    assert seen[0][0] == {"query": "x"}


@pytest.mark.asyncio
async def test_recall_without_a_query_is_refused_before_the_database(monkeypatch):
    """A required arg the skill cannot run without, named before a session is
    opened — the same discipline the write paths use."""
    def _never(*_a, **_k):
        raise AssertionError("should not have opened a session")

    monkeypatch.setattr(peer_memory, "AsyncSessionLocal", _never)

    response = await peer_memory.run_memory_command("optimus", {
        "request_id": "s4", "skill": "recall_conversations", "query": "   ",
    })

    assert response["ok"] is False
    assert "query" in response["result"]


@pytest.mark.asyncio
async def test_an_empty_channel_is_a_successful_read_not_a_failure(monkeypatch):
    """'The channel is empty' is a real answer written for a model, not a
    backend failure the peer should retry."""
    _read_skill(monkeypatch, "read_agent_channel",
                "The agent network channel is empty — no inter-agent traffic yet.")

    response = await peer_memory.run_memory_command("optimus", {
        "request_id": "s5", "skill": "read_agent_channel",
    })

    assert response["ok"] is True
    assert "empty" in response["result"]


@pytest.mark.asyncio
async def test_a_read_skill_that_could_not_run_is_a_failure(monkeypatch):
    class _Broken:
        async def execute(self, args, context):
            raise RuntimeError("the embedder is down")

    monkeypatch.setitem(
        peer_memory._READ_SKILLS,                                  # noqa: SLF001
        "recall_conversations",
        peer_memory._ReadSkill(_Broken, keys=("query",), required=("query",)),  # noqa: SLF001
    )

    response = await peer_memory.run_memory_command("optimus", {
        "request_id": "s6", "skill": "recall_conversations", "query": "x",
    })

    assert response["ok"] is False
    assert "the embedder is down" in response["result"]


def test_the_read_routes_track_the_skills_they_claim_to_run():
    """The route's declared arg keys must be a subset of what the skill's schema
    accepts — a key the skill never reads is a silent drop, the exact failure
    the argument allowlist exists to prevent."""
    from app.skills.dispatch import AgentChannelSkill
    from app.skills.semantic_search import SemanticSearchSkill

    pairs = {
        "recall_conversations": SemanticSearchSkill,
        "read_agent_channel": AgentChannelSkill,
    }
    for name, cls in pairs.items():
        route = peer_memory._READ_SKILLS[name]                     # noqa: SLF001
        assert route.factory is cls
        declared = set(cls.input_schema["properties"])
        assert set(route.keys) <= declared, (name, set(route.keys) - declared)
        assert set(route.required) <= set(route.keys)
