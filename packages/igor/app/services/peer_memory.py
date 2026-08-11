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
from app.skills.memory import MemorySkill

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
    """
    request_id = str(frame.get("request_id", ""))
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
