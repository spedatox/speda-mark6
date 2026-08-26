"""Peer memory — running one memory command on behalf of an external agent.

Optimus is the one agent that does not run in this process (CLAUDE.md), and the
memory redesign is what made that matter. The block Igor injects into every turn
stopped being "here is everything about the owner" and became "here is a
directory listing and four preloaded files; use the `memory` tool for the rest".
Every in-process profile has that tool from the CapabilityRegistry. The peer
gets the block over a socket and had no tool at all — so the instruction named
something that did not exist on its side, and the agent could not open a project
file, look up a person, read a ledger, or write down anything it learned.

This is the missing half. The peer sends `memory_request`, this executes the
SAME `MemorySkill` every in-process agent uses, and the router sends back
`memory_response`.

**Read skills ride the same frame.** The memory redesign gave in-process agents
more than the `memory` tool: `recall_conversations` searches the owner's ENTIRE
history by meaning, and `read_agent_channel` reads the shared inter-agent log —
and the block Igor injects names both by name (`prompts/core/08_memory.md`,
`09_agent_network.md`). The peer got the block and had neither tool, so the same
gap that made `memory` necessary applies to these: an agent told to recall a
past conversation, or to check what another agent has been doing, was being told
to use something it did not have. A frame carrying `"skill": "recall_conversations"`
or `"skill": "read_agent_channel"` runs the SAME skill in-process agents run —
read-only, so it needs neither the file law's write checks nor an observation's
shape, only the query arguments the skill declares. This is also how the two
Forge peers come to know each other: Optimus reading the channel sees
Centurion's traffic and vice versa, through the one skill that already renders it.

**A second write skill rides the same frame.** A Forge session that notices
something about the owner while offline queues it locally
(forge/agents/owner_memory.py's `pending_observations.jsonl`) and, once
reconnected, flushes the queue as `memory_request` frames carrying
`"skill": "record_observation"` instead of a `command`. This runs the SAME
`RecordObservationSkill` any in-process agent uses — the fact lands in the
observation record authored as `optimus` (Rule 18 enforcement applies
identically; Optimus owns no domain folder, so it can never write one via this
path either), and Orion's regular nightly audit consolidates it exactly like
any other agent's observation. Nothing here decides what the fact means or
whether it survives — that judgement stays Orion's, on its own schedule.

**The same skill, deliberately.** Not a reimplementation and not a subset. Path
validation, the file law, per-document ownership, the revision trail and the
schema advisories are all enforced here exactly as they are for Sentinel or
Atomix — which means an external agent cannot reach around a rule by being
external. A second implementation would be a second answer to the same question
the first time either side was edited, and the divergence would show up as the
owner's memory disagreeing with itself.

**Its own session per command.** The agent WebSocket holds one request-scoped
session for the entire life of a connection that stays up for days; committing a
memory write on it would tie an audit row to a transaction of unknown age and
share failure state across everything else that connection has done. A memory
command is a short unit of work and gets a short unit of work's session.

**Igor decides nothing about the answer.** Whatever the skill returns is what
goes back, verbatim — including its refusals, which are written to be read by a
model. What this adds is the one bit the skill has no way to express: whether
the command ran at all, so a peer never reports a write as filed when the
write never happened.
"""

import logging
import uuid

from app.core.context import AgentContext
from app.database import AsyncSessionLocal
from app.skills.dispatch import AgentChannelSkill
from app.skills.memory import MemorySkill
from app.skills.observations import RecordObservationSkill
from app.skills.semantic_search import SemanticSearchSkill

logger = logging.getLogger(__name__)

# Single-user system (CLAUDE.md), the same constant the Telegram gateway and the
# chat router already use. The peer is authenticated by the shared API key on the
# WebSocket handshake and speaks for the owner; there is no second user to be.
_OWNER_USER_ID = 1

_VALID_COMMANDS = frozenset({"view", "create", "str_replace", "insert", "delete"})

_ARGUMENT_KEYS = (
    "command", "path", "file_text", "old_str", "new_str",
    "insert_line", "insert_text", "view_range",
)
"""What a memory command may carry. Taken from the frame explicitly rather than
passed through wholesale: the frame also carries routing fields (request_id,
chat_id, type) that are Igor's business and not the skill's, and a peer that
one day adds a field must not be able to reach the skill's kwargs with it."""

_OBSERVATION_KEYS = (
    "content", "level", "source_ids", "premises", "sources",
    "pattern_type", "confidence", "subject", "domain",
    "valid_from", "valid_until", "supersedes",
)
"""One observation's fields, per RecordObservationSkill.input_schema. A queued
Forge fact is always ONE fact per frame (owner_memory.py flushes its local
queue line by line), so this wraps into the single-element `observations` list
the skill expects rather than exposing Forge to that shape directly."""


class _ReadSkill:
    """One read-only skill the peer may reach over the memory frame.

    `keys` is the allowlist the same way `_ARGUMENT_KEYS` is: the frame also
    carries routing fields (request_id, chat_id, type, skill) that are Igor's,
    not the skill's, and a peer that one day adds a field must not reach the
    skill's kwargs with it. `required` is the minimum the skill cannot run
    without — checked here so an obviously empty call is named before a session
    is opened, exactly as the write paths do.
    """

    __slots__ = ("factory", "keys", "required")

    def __init__(self, factory, keys: tuple[str, ...], required: tuple[str, ...] = ()):
        self.factory = factory
        self.keys = keys
        self.required = required


# The read skills reachable over the peer frame, keyed by the `skill` field. Each
# runs the SAME class an in-process agent runs, so recall and the agent channel
# answer an external agent exactly as they answer Sentinel — there is no second
# implementation to drift. Read-only: no file law, no revision trail, no
# observation shape, only the query the skill declares.
_READ_SKILLS: dict[str, _ReadSkill] = {
    "recall_conversations": _ReadSkill(
        SemanticSearchSkill,
        keys=("query", "after", "before", "agent_id", "context_window", "limit"),
        required=("query",),
    ),
    "read_agent_channel": _ReadSkill(
        AgentChannelSkill,
        keys=("limit", "agent"),
    ),
}


def _context(agent_id: str, db, request_id: str) -> AgentContext:
    """The minimum AgentContext the memory path actually reads.

    `agent_id` is the load-bearing field and the reason this is not a dict: the
    file law records which agent owns which document, and `check_write` refuses
    on the author. Optimus writing the finance ledger is refused for exactly the
    same reason and by exactly the same code as Ultron writing it would be.
    """
    return AgentContext(
        agent_id=agent_id,
        user_id=_OWNER_USER_ID,
        session_id=0,                 # a memory command belongs to no session
        request_id=request_id,
        triggered_by="agent",
        trigger_payload={},
        output_mode="silent",
        model="",                     # nothing here calls a model
        system_prompt="",
        conversation_history=[],
        db=db,
    )


async def run_memory_command(agent_id: str, frame: dict) -> dict:
    """Execute one peer memory command. Returns the `memory_response` payload.

    Never raises. A peer is parked on this inside one tool dispatch, and an
    exception that escaped would leave it waiting out its own timeout with
    nothing to tell the model — the worst available outcome for a WRITE, where
    silence and success are indistinguishable from the far side.

    `frame["skill"]` picks which skill runs — absent or `"memory"` is the
    original flat MemorySkill path; `"record_observation"` is the Forge
    pending-observations flush; `"recall_conversations"` and
    `"read_agent_channel"` are the read skills (see module docstring). Same
    envelope every way: one request_id, one `memory_response`, the skill's own
    text verbatim.
    """
    request_id = str(frame.get("request_id", ""))
    skill_name = str(frame.get("skill") or "memory")

    if skill_name == "record_observation":
        return await _run_record_observation(agent_id, frame, request_id)
    read = _READ_SKILLS.get(skill_name)
    if read is not None:
        return await _run_read_skill(agent_id, frame, request_id, skill_name, read)
    return await _run_memory_skill(agent_id, frame, request_id)


async def _run_memory_skill(agent_id: str, frame: dict, request_id: str) -> dict:
    command = str(frame.get("command", ""))
    path = str(frame.get("path", ""))

    if command not in _VALID_COMMANDS:
        return {
            "type": "memory_response",
            "request_id": request_id,
            "ok": False,
            "result": f"Unknown memory command {command!r}. "
                      f"Valid: {', '.join(sorted(_VALID_COMMANDS))}.",
        }

    args = {k: frame[k] for k in _ARGUMENT_KEYS if k in frame}

    try:
        async with AsyncSessionLocal() as db:
            context = _context(agent_id, db, request_id or uuid.uuid4().hex)
            result = await MemorySkill().execute(args, context)
    except Exception as e:  # noqa: BLE001 — see docstring: a peer is waiting
        logger.exception(
            "peer_memory_failed",
            extra={"agent_id": agent_id, "command": command, "path": path},
        )
        return {
            "type": "memory_response",
            "request_id": request_id,
            "ok": False,
            "result": f"The memory backend failed to run this command: "
                      f"{type(e).__name__}: {e}",
        }

    logger.info(
        "peer_memory_command",
        extra={"agent_id": agent_id, "command": command, "path": path},
    )
    # ok=True means "the command ran", not "you got what you wanted". A refusal
    # the skill wrote — a path that does not exist, an old_str that appears
    # twice, a document this agent does not own — is a real answer and reaches
    # the model as its result. Only a command that could not be run at all is
    # a failure, because that is the only case where the peer must not believe
    # anything about what happened.
    return {
        "type": "memory_response",
        "request_id": request_id,
        "ok": True,
        "result": result,
    }


async def _run_record_observation(agent_id: str, frame: dict, request_id: str) -> dict:
    observation = {k: frame[k] for k in _OBSERVATION_KEYS if k in frame}
    if not observation.get("content") or not observation.get("level") or not observation.get("domain"):
        return {
            "type": "memory_response",
            "request_id": request_id,
            "ok": False,
            "result": "record_observation needs at least content, level and domain.",
        }

    try:
        async with AsyncSessionLocal() as db:
            context = _context(agent_id, db, request_id or uuid.uuid4().hex)
            result = await RecordObservationSkill().execute(
                {"observations": [observation]}, context,
            )
    except Exception as e:  # noqa: BLE001 — see run_memory_command's docstring
        logger.exception(
            "peer_memory_observation_failed",
            extra={"agent_id": agent_id, "domain": observation.get("domain")},
        )
        return {
            "type": "memory_response",
            "request_id": request_id,
            "ok": False,
            "result": f"The memory backend failed to record this: "
                      f"{type(e).__name__}: {e}",
        }

    logger.info(
        "peer_memory_observation",
        extra={"agent_id": agent_id, "domain": observation.get("domain")},
    )
    return {
        "type": "memory_response",
        "request_id": request_id,
        "ok": True,
        "result": result,
    }


async def _run_read_skill(
    agent_id: str, frame: dict, request_id: str, skill_name: str, read: _ReadSkill,
) -> dict:
    """Run one read-only peer skill — recall or the agent channel.

    Read skills report `ok=True` on any result the skill produced, refusals
    included, for the same reason the write paths do: "no relevant past
    exchanges" and "the channel is empty" are real answers written to be read by
    a model, not backend failures the peer should retry. `ok=False` is reserved,
    exactly as elsewhere, for the one case where the command could not run at all
    and the peer must believe nothing about what happened.
    """
    args = {k: frame[k] for k in read.keys if k in frame}
    missing = [k for k in read.required if not str(args.get(k, "")).strip()]
    if missing:
        return {
            "type": "memory_response",
            "request_id": request_id,
            "ok": False,
            "result": f"{skill_name} needs at least {', '.join(read.required)}.",
        }

    try:
        async with AsyncSessionLocal() as db:
            context = _context(agent_id, db, request_id or uuid.uuid4().hex)
            result = await read.factory().execute(args, context)
    except Exception as e:  # noqa: BLE001 — see run_memory_command's docstring
        logger.exception(
            "peer_memory_read_failed",
            extra={"agent_id": agent_id, "skill": skill_name},
        )
        return {
            "type": "memory_response",
            "request_id": request_id,
            "ok": False,
            "result": f"The memory backend failed to run {skill_name}: "
                      f"{type(e).__name__}: {e}",
        }

    logger.info(
        "peer_memory_read",
        extra={"agent_id": agent_id, "skill": skill_name},
    )
    return {
        "type": "memory_response",
        "request_id": request_id,
        "ok": True,
        "result": result,
    }
