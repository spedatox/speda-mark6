# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Inter-agent dispatch — the orchestrator-routed primitive (CLAUDE.md: agents
never talk to each other directly; everything goes through here).

An agent calls the dispatch_agent tool → AgentDispatcher.dispatch() runs the
TARGET agent's own orchestrator loop in-process (its profile, its prompt, its
model policy) and returns the final text to the CALLER. Multi-agent fan-out is
just the caller emitting several dispatch_agent tool calls in one turn — the
orchestrator already executes tool calls in parallel — or broadcast() under the
House Party Protocol, which rallies every in-process agent at once.

House Party Protocol (runtime flag, app/core/runtime_state.py): when engaged,
dispatched agents run at full interactive model grade instead of the background
tier, and broadcast dispatch ("all") becomes available. The flag is toggled by
the owner from the UI (POST /agents/house-party) or by an agent via the
house_party tool.

External peers (Optimus deployed standalone) are reached over the WebSocket
channel via WebSocketManager. A CONNECTED peer always outranks its in-process
profile — the profile remains as the identity layer and offline fallback. The
task is sent as a `task_dispatch` frame (optionally carrying a working
directory for coding work) and the result awaited on a correlated
`task_result` frame.

A dispatched run is a REAL, READABLE CONVERSATION on the target agent, exactly
as an n8n-triggered run is (app/core/trigger_runner.py): the incoming task is
persisted as the opening user turn — attributed to the calling agent, never to
the owner — the session is named at launch, and the reply is persisted with its
tool calls. Before this, the run created a session row and streamed into a
throwaway list, so the owner found a "New conversation" in the target's sidebar
that opened empty while the work itself only survived as a comms-tray ticket.

Every dispatch — and its result — is logged to the agent_messages table for the
comms tray (GET /agents/comms). Telemetry writes are best-effort: a logging
failure never fails the dispatch itself.

A BACKGROUND dispatch (spawn) reports back when it lands: the report hook wired
in the lifespan starts a push turn on the agent that sent it, so the answer is
read and delivered unprompted. Without it a backgrounded job ended in silence
and the owner had to ask "is it done?" — which is exactly the polling that
sending the job away was supposed to remove.

Construction: created BEFORE the CapabilityRegistry (the dispatch_agent skill
needs it at registration time), wired AFTER the orchestrator exists via wire().
All references live on app.state per Rule 6 — this module holds no globals.
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

from app.config import settings
from app.core.context import AgentContext
from app.core.peer_routing import resolve
from app.core.runtime_state import get_house_party
from app.database import AsyncSessionLocal
from app.models.agent_message import AgentMessage
from app.schemas.sse import SSEEventType

logger = logging.getLogger(__name__)

# An agent may dispatch, and the dispatched agent may dispatch once more —
# beyond that the chain is refused (runaway-fan-out guard, mirrors Rule 4a).
MAX_DISPATCH_DEPTH = 2
EXTERNAL_TIMEOUT_S = 180.0          # WebSocket peers must answer within this window
EXTERNAL_CODING_TIMEOUT_S = 600.0   # coding peers (Optimus) get room for real work
MAX_RESULT_CHARS = 12_000           # cap what flows back into the caller's context
MAX_BACKGROUND = 5                  # concurrent background (spawned) dispatches
# Hard ceiling on a detached background dispatch. Nothing else bounds an
# in-process run (external peers have their own timeouts), so without this a
# wedged orchestrator loop leaves its row on "running" indefinitely.
BACKGROUND_TIMEOUT_S = 900.0

# The group channel: how much recent network traffic a dispatched agent sees,
# and how hard each entry is truncated inside the transcript. Keeps the shared
# context useful without bloating every dispatch prompt.
CHANNEL_WINDOW = 20
CHANNEL_TASK_CHARS = 280
CHANNEL_RESULT_CHARS = 340

# The owner-facing "run this in the background" command. Defined once here
# because two entry points parse it (the Telegram gateway and the web chat
# router) and a command that means one thing in one surface and nothing in the
# other is the kind of drift a single source of truth exists to prevent.
BG_COMMAND = "/bg"


@dataclass
class SpawnOutcome:
    """What `spawn()` hands back: a structured result, not a status to be
    recovered by matching prose.

    `message` is still the model-facing text the `dispatch_agent` tool relays
    verbatim (it tells the model not to fabricate the result, to report back
    when woken, etc.). `started`/`ticket_id` are for callers that need to BRANCH
    on the outcome — the `/bg` handlers, which show the owner a confirmation on
    success and the refusal reason on failure, and must not read either out of
    the message string."""
    started: bool
    message: str
    ticket_id: int | None = None


def bg_ack(outcome: "SpawnOutcome") -> str:
    """The owner-facing reply to a `/bg` command, shared by every surface that
    accepts one so they cannot drift.

    On refusal it shows the dispatcher's own reason verbatim. On success it does
    NOT echo the task back — the owner just typed it — and it hands over the
    ticket id so progress is checkable on demand from the same conversation
    (ask the agent, which reads it via dispatch_status; the finished result also
    lands back in this conversation on its own)."""
    if not outcome.started:
        return outcome.message
    handle = f" (#{outcome.ticket_id})" if outcome.ticket_id is not None else ""
    return (
        f"On it — running in the background{handle}. "
        "Ask me how it's going whenever you like; I'll also report back here "
        "the moment it lands."
    )


async def channel_transcript(
    limit: int = CHANNEL_WINDOW, agent: str | None = None, exclude_id: int | None = None,
) -> str:
    """
    The agent network's GROUP CHAT, rendered from agent_messages: every dispatch
    and its reply, oldest→newest, formatted as channel lines. Injected into each
    dispatched agent's context (so the roster shares one conversation) and
    served to the read_agent_channel skill. Returns "" when there is no traffic.
    """
    try:
        async with AsyncSessionLocal() as db:
            stmt = select(AgentMessage).order_by(AgentMessage.id.desc()).limit(max(1, min(limit, 60)))
            if exclude_id is not None:
                stmt = stmt.where(AgentMessage.id != exclude_id)
            if agent:
                from sqlalchemy import or_
                stmt = stmt.where(or_(AgentMessage.from_agent == agent, AgentMessage.to_agent == agent))
            rows = list((await db.execute(stmt)).scalars().all())
    except Exception as e:  # noqa: BLE001 — the channel is flavour, never load-bearing
        logger.warning("channel_transcript_failed", extra={"error": str(e)})
        return ""
    if not rows:
        return ""

    lines: list[str] = []
    for r in reversed(rows):  # oldest first, like a chat scrollback
        ts = r.created_at.strftime("%m-%d %H:%M")
        task = " ".join(r.task.split())[:CHANNEL_TASK_CHARS]
        lines.append(f"[{ts}] {r.from_agent.upper()} → {r.to_agent.upper()}: {task}")
        if r.status == "running":
            lines.append(f"         └ {r.to_agent.upper()}: … (still working)")
        elif r.result:
            result = " ".join(r.result.split())[:CHANNEL_RESULT_CHARS]
            tag = "" if r.status == "ok" else f" [{r.status.upper()}]"
            lines.append(f"         └ {r.to_agent.upper()}{tag}: {result}")
    return "\n".join(lines)


def session_title(from_agent: str, task: str) -> str:
    """Name a dispatched session from who sent it and what they asked, at launch.

    Set up front rather than left to generate_title, for the same reason a
    triggered run names itself (trigger_runner.session_title): the owner opens
    the target agent's sidebar and must be able to tell an incoming dispatch
    from their own conversations without clicking each one. An untitled session
    renders as "New conversation", which is what sent them looking in the first
    place.
    """
    snippet = " ".join(task.split())[:64]
    return f"{from_agent.upper()} → {snippet}"[:255]


def dispatch_meta(from_agent: str, house_party: bool) -> dict:
    """Display-only provenance for the opening turn of a dispatched session,
    carried in the `_speda_meta` block the UI already recovers on reload
    (services/chat_history.py, Heartbreaker's Message.tsx).

    The owner did not write this turn — another agent did — and a bubble
    attributed to them would be a lie the next reader has no way to catch. Same
    contract as an automation's seed, with `source="agent"` instead of "n8n" and
    the caller named in `from_agent`. `_clean` strips the block before the
    history goes back to the model, so it costs nothing in the prompt.
    """
    return {
        "source": "agent",
        "from_agent": from_agent,
        "label": f"Dispatch from {from_agent.upper()}",
        "job": "house party dispatch" if house_party else "dispatch",
    }


async def sweep_stale_dispatches() -> int:
    """
    Close out every exchange still marked `running` at startup.

    A dispatch only ever runs inside this process, so a row left on "running"
    when we boot belongs to a run that died with the previous process (crash,
    redeploy, kill -9) — it is not working, and left alone it sits in the comms
    tray claiming to be live forever. Called from the lifespan handler before
    the app serves traffic. Returns how many rows were closed.
    """
    from sqlalchemy import update

    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                update(AgentMessage)
                .where(AgentMessage.status == "running")
                .values(
                    status="interrupted",
                    result=(
                        "Interrupted — the backend restarted while this was running. "
                        "The task did not finish; re-run it if the result is still needed."
                    ),
                )
            )
            await db.commit()
            closed = int(result.rowcount or 0)
    except Exception as e:  # noqa: BLE001 — telemetry cleanup never blocks startup
        logger.warning("dispatch_sweep_failed", extra={"error": str(e)})
        return 0
    if closed:
        logger.info("dispatch_sweep", extra={"closed": closed})
    return closed


class AgentDispatcher:
    """Runs inter-agent tasks and logs the traffic. One instance on app.state."""

    def __init__(self) -> None:
        self._orchestrator = None
        self._profiles = None
        self._session_manager = None
        self._ws_manager = None
        # Correlation futures for external (WebSocket) dispatches, keyed by task_id.
        self._pending_external: dict[str, asyncio.Future] = {}
        # Live background (spawned) dispatch tasks — tracked so they aren't GC'd
        # mid-run and can be capped/cancelled on shutdown.
        self._background: set[asyncio.Task] = set()
        # Set late in the lifespan (see set_report_hook): the callback closes
        # over the orchestrator and turn registry, neither of which exists when
        # the dispatcher is constructed (it precedes the registry).
        self._report_hook = None

    def wire(self, *, orchestrator, profiles, session_manager, ws_manager) -> None:
        """Late-bind the engine refs (they are constructed after the registry)."""
        self._orchestrator = orchestrator
        self._profiles = profiles
        self._session_manager = session_manager
        self._ws_manager = ws_manager

    def set_report_hook(self, hook) -> None:
        """Install the callback a finished BACKGROUND dispatch fires to report in.

        Kept as an injected awaitable rather than logic here, exactly as the
        Legion's is (app/legion/runner.py): this module runs agents and must not
        learn about sessions, delivery or the turn registry (Rule 1). None = the
        old behaviour, where a background result waits in its ticket until
        someone thinks to call dispatch_status.
        """
        self._report_hook = hook

    # ── Public API ───────────────────────────────────────────────────────────

    def known_agents(self) -> list[str]:
        """agent_ids of every dispatchable in-process profile (for tool schemas /
        validation). Session-scope aliases (dispatch_target=False) are excluded —
        they take /chat requests but are never dispatch targets."""
        if self._profiles is None:
            return []
        return [p.agent_id for p in self._profiles.roster() if p.dispatch_target]

    async def push_to_peers(self, agent_id: str, frame: dict) -> int:
        """Fire-and-forget a frame to every connected host for `agent_id`.

        For unsolicited server-initiated pushes (the owner-memory sync to
        Forge, not a request/response like task_dispatch) — there is no
        result to correlate, only "how many peers got this". Returns 0 (and
        sends nothing) when the dispatcher has not been wired yet or nobody
        is connected; a caller checks that to know whether to tell the model
        the push actually reached anyone.
        """
        if self._ws_manager is None:
            return 0
        hosts = self._ws_manager.hosts(agent_id)
        for host in hosts:
            await self._ws_manager.send(agent_id, frame, host=host)
        return len(hosts)

    async def dispatch(
        self,
        *,
        from_agent: str,
        to_agent: str,
        task: str,
        user_id: int,
        request_id: str,
        depth: int = 0,
        cwd: str | None = None,
        origin_session_id: int | None = None,
    ) -> str:
        """
        Run `task` on `to_agent` and return its final text to the caller.
        Never raises — every failure path returns an explanatory string the
        calling model can reason over (same tolerance pattern as skills).
        """
        precheck = self._precheck(from_agent, to_agent, depth)
        if precheck is not None:
            return precheck

        protocol = "house_party" if get_house_party() else "direct"
        msg_id = await self._log_start(
            request_id=request_id, from_agent=from_agent, to_agent=to_agent,
            kind="dispatch", protocol=protocol, task=task,
            origin_session_id=origin_session_id,
        )
        try:
            result, status, session_id, duration_ms = await self._execute(
                from_agent=from_agent, to_agent=to_agent, task=task, user_id=user_id,
                request_id=request_id, depth=depth, protocol=protocol, cwd=cwd,
                own_msg_id=msg_id, origin_session_id=origin_session_id,
            )
        except asyncio.CancelledError:
            # The caller's turn was stopped (owner hit stop, client vanished,
            # shutdown). Close the row out — an abandoned dispatch that stays
            # "running" is exactly the ghost the comms tray used to show.
            await self._log_finish(
                msg_id, status="cancelled", session_id=None, duration_ms=0,
                result="Cancelled — the turn that ordered this dispatch was stopped.",
            )
            raise
        await self._log_finish(
            msg_id, status=status, result=result, session_id=session_id, duration_ms=duration_ms,
        )
        logger.info(
            "agent_dispatch",
            extra={
                "request_id": request_id, "from": from_agent, "to": to_agent,
                "status": status, "protocol": protocol, "depth": depth,
            },
        )
        return result

    def _precheck(self, from_agent: str, to_agent: str, depth: int,
                  *, allow_self: bool = False) -> str | None:
        """Shared guard for dispatch() and spawn(). Returns an error string to
        relay, or None when the dispatch may proceed.

        `allow_self` exists for exactly one caller: an owner-authenticated
        `/bg` command typed directly to an agent's own channel, where "dispatch
        to yourself" is the whole request, not a model deciding to loop. The
        model-facing `dispatch_agent` tool never sets it, so a model still
        cannot talk itself into self-dispatching — the guard below is unchanged
        for that path. The report turn this produces (agent_id == to_agent) is
        already handled correctly by `_start_report_turn`'s room-mismatch
        fallback (trigger_runner.py), which exists for the case one arm of a
        chain backgrounds a dispatch of its own — self-dispatch is just the
        shortest such chain.
        """
        if self._orchestrator is None:
            return "Dispatch engine is not wired yet — backend still starting up."
        if to_agent == from_agent and not allow_self:
            return "Refused: you cannot dispatch a task to yourself. Do it directly."
        if depth >= MAX_DISPATCH_DEPTH:
            return (
                f"Refused: dispatch depth limit ({MAX_DISPATCH_DEPTH}) reached. "
                "Complete the task yourself instead of delegating further."
            )
        return None

    async def _execute(
        self, *, from_agent: str, to_agent: str, task: str, user_id: int,
        request_id: str, depth: int, protocol: str, cwd: str | None, own_msg_id: int | None,
        origin_session_id: int | None = None,
    ) -> tuple[str, str, int | None, int]:
        """The dispatch body: external-first routing with in-process fallback.
        Returns (result, status, session_id, duration_ms). Shared by the
        synchronous dispatch() and the background spawn() path."""
        started = time.monotonic()
        # External-first: a connected standalone peer (Optimus) always outranks
        # its in-process profile — the profile stays registered as the identity
        # layer and the offline fallback, never as a competing engine.
        profile = self._profiles.get(to_agent) if self._profiles else None
        external = self._ws_manager is not None and self._ws_manager.is_connected(to_agent)
        session_id = None
        if external:
            result, status = await self._run_external(
                to_agent=to_agent, from_agent=from_agent, task=task, cwd=cwd,
            )
            if status in ("offline", "error") and profile is not None:
                # Peer vanished between the presence check and the send —
                # degrade to the in-process profile rather than failing.
                # "refused" is deliberately absent: that status means the work
                # named a directory no connected peer covers, and running it
                # in-process would put it on the server anyway (see
                # _run_external / app/core/peer_routing.py).
                result, status, session_id = await self._run_in_process(
                    profile=profile, from_agent=from_agent, task=task,
                    user_id=user_id, request_id=request_id, depth=depth,
                    house_party=(protocol == "house_party"), own_msg_id=own_msg_id,
                    origin_session_id=origin_session_id,
                )
        elif profile is not None:
            result, status, session_id = await self._run_in_process(
                profile=profile, from_agent=from_agent, task=task,
                user_id=user_id, request_id=request_id, depth=depth,
                house_party=(protocol == "house_party"), own_msg_id=own_msg_id,
                origin_session_id=origin_session_id,
            )
        else:
            result, status = await self._run_external(
                to_agent=to_agent, from_agent=from_agent, task=task, cwd=cwd,
            )
        return result, status, session_id, int((time.monotonic() - started) * 1000)

    async def spawn(
        self,
        *,
        from_agent: str,
        to_agent: str,
        task: str,
        user_id: int,
        request_id: str,
        depth: int = 0,
        cwd: str | None = None,
        origin_session_id: int | None = None,
        allow_self: bool = False,
    ) -> SpawnOutcome:
        """Background dispatch: start `task` on `to_agent` in a detached task and
        return IMMEDIATELY so the caller's own turn can finish. The result lands
        in the agent channel / comms tray (status running → ok) and is
        retrievable via dispatch_status. Never raises.

        Returns a `SpawnOutcome`: `.message` is the text a model-facing caller
        relays, `.started`/`.ticket_id` let an owner-facing caller branch. See
        `SpawnOutcome`.

        `allow_self`: see `_precheck`. False for every model-facing caller
        (`dispatch_agent`); the owner-typed `/bg` command is the one caller
        that passes True."""
        precheck = self._precheck(from_agent, to_agent, depth, allow_self=allow_self)
        if precheck is not None:
            return SpawnOutcome(started=False, message=precheck)
        live = sum(1 for t in self._background if not t.done())
        if live >= MAX_BACKGROUND:
            return SpawnOutcome(started=False, message=(
                f"Refused: already running {live} background dispatches (max "
                f"{MAX_BACKGROUND}). Wait for one to finish, or run this one inline."
            ))

        protocol = "house_party" if get_house_party() else "direct"
        msg_id = await self._log_start(
            request_id=request_id, from_agent=from_agent, to_agent=to_agent,
            kind="dispatch", protocol=protocol, task=task,
            origin_session_id=origin_session_id,
        )
        task_obj = asyncio.create_task(self._run_and_finish(
            msg_id=msg_id, from_agent=from_agent, to_agent=to_agent, task=task,
            user_id=user_id, request_id=request_id, depth=depth, protocol=protocol, cwd=cwd,
            origin_session_id=origin_session_id,
        ))
        self._background.add(task_obj)
        task_obj.add_done_callback(self._background.discard)
        ticket = f"#{msg_id}" if msg_id is not None else "(untracked)"
        return SpawnOutcome(started=True, ticket_id=msg_id, message=(
            f"Background dispatch {ticket} → {to_agent.upper()} started. The result "
            "is NOT available yet, so never guess or fabricate it. When "
            f"{to_agent.upper()} finishes you will be woken with its answer and you "
            "will deliver it to the owner then — so tell him it is running and that "
            "you will report back, and do NOT promise to check on it yourself or ask "
            f"him to remind you. dispatch_status (id={msg_id}) is only for when he "
            "asks before it lands."
        ))

    async def _run_and_finish(
        self, *, msg_id: int | None, from_agent: str, to_agent: str, task: str,
        user_id: int, request_id: str, depth: int, protocol: str, cwd: str | None,
        origin_session_id: int | None = None,
    ) -> None:
        """Detached body of a background dispatch: execute, then log the result.

        EVERY exit path finalizes the row — success, failure, the hard timeout,
        and cancellation on shutdown. Nothing else is watching this task, so a
        path that skips the finish log leaves a ticket "running" forever."""
        started = time.monotonic()
        try:
            result, status, session_id, duration_ms = await asyncio.wait_for(
                self._execute(
                    from_agent=from_agent, to_agent=to_agent, task=task, user_id=user_id,
                    request_id=request_id, depth=depth, protocol=protocol, cwd=cwd,
                    own_msg_id=msg_id, origin_session_id=origin_session_id,
                ),
                timeout=BACKGROUND_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.warning("agent_dispatch_bg_timeout", extra={"request_id": request_id, "to": to_agent})
            result, status, session_id = (
                f"Timed out — {to_agent} was still working after "
                f"{int(BACKGROUND_TIMEOUT_S / 60)} minutes and the dispatch was abandoned.",
                "timeout", None,
            )
            duration_ms = int((time.monotonic() - started) * 1000)
        except asyncio.CancelledError:
            await self._log_finish(
                msg_id, status="cancelled", session_id=None,
                duration_ms=int((time.monotonic() - started) * 1000),
                result="Cancelled — the backend shut down while this was running.",
            )
            raise
        except Exception as e:  # noqa: BLE001 — a background dispatch must never crash the loop
            logger.error("agent_dispatch_bg_error", extra={"request_id": request_id, "to": to_agent, "error": str(e)})
            result, status, session_id, duration_ms = f"Background dispatch failed: {e}", "error", None, 0
        await self._log_finish(
            msg_id, status=status, result=result, session_id=session_id, duration_ms=duration_ms,
        )
        logger.info(
            "agent_dispatch_bg_done",
            extra={"request_id": request_id, "from": from_agent, "to": to_agent, "status": status},
        )

        # Report in. The ticket is written FIRST so the result is durable even if
        # the reporting turn cannot start (registry at capacity, provider down) —
        # it is still retrievable with dispatch_status. Cancellation is not
        # reported: that path is a shutdown, not a finding, and it returns above
        # before reaching here.
        if self._report_hook is not None:
            try:
                await self._report_hook(
                    agent_id=from_agent,
                    to_agent=to_agent,
                    task=task,
                    result=result,
                    status=status,
                    ticket=msg_id,
                    # Report back into the conversation this was ordered from,
                    # not a blank session: that thread holds the owner's
                    # constraints and the reason the job was sent away.
                    room_session_id=origin_session_id,
                )
            except Exception as e:  # noqa: BLE001 — never break on delivery
                logger.error(
                    "dispatch_report_failed",
                    extra={"request_id": request_id, "to": to_agent, "error": str(e)},
                )

    async def broadcast(
        self,
        *,
        from_agent: str,
        task: str,
        user_id: int,
        request_id: str,
        depth: int = 0,
        background: bool = False,
        origin_session_id: int | None = None,
    ) -> str:
        """
        House Party fan-out: run `task` on every in-process agent except the
        caller, in parallel, and return the combined transcripts. Only available
        while the House Party Protocol is engaged — direct one-to-one dispatch
        needs no protocol. With background=True each target is spawned detached
        and the method returns tickets immediately.
        """
        if not get_house_party():
            return (
                "Refused: broadcast dispatch requires the House Party Protocol. "
                "Ask the owner to engage it (comms tray or /agents/house-party), "
                "or engage it with the house_party tool if the owner asked for it."
            )
        targets = [a for a in self.known_agents() if a != from_agent]
        if not targets:
            return "No other agents are registered."

        if background:
            outcomes = await asyncio.gather(*[
                self.spawn(
                    from_agent=from_agent, to_agent=t, task=task,
                    user_id=user_id, request_id=request_id, depth=depth,
                    origin_session_id=origin_session_id,
                )
                for t in targets
            ])
            return ("House Party broadcast dispatched in the background:\n\n"
                    + "\n".join(o.message for o in outcomes))

        await self._log_start(
            request_id=request_id, from_agent=from_agent, to_agent="all",
            kind="broadcast", protocol="house_party", task=task, status="ok",
            origin_session_id=origin_session_id,
        )
        results = await asyncio.gather(*[
            self.dispatch(
                from_agent=from_agent, to_agent=t, task=task,
                user_id=user_id, request_id=request_id, depth=depth,
                origin_session_id=origin_session_id,
            )
            for t in targets
        ])
        parts = [f"### {t.upper()}\n{r}" for t, r in zip(targets, results)]
        return "House Party broadcast complete. Responses:\n\n" + "\n\n".join(parts)

    async def shutdown(self) -> None:
        """Cancel any in-flight background dispatches on app shutdown."""
        tasks = [t for t in self._background if not t.done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def resolve_external_result(self, task_id: str, result: str, status: str = "ok") -> bool:
        """Called by the agents WebSocket router when a `task_result` frame
        arrives from an external peer. Returns True if a dispatch was waiting."""
        fut = self._pending_external.get(task_id)
        if fut is None or fut.done():
            return False
        fut.set_result((result, status if status in ("ok", "error") else "ok"))
        return True

    # ── In-process path ──────────────────────────────────────────────────────

    async def _run_in_process(
        self, *, profile, from_agent: str, task: str,
        user_id: int, request_id: str, depth: int, house_party: bool,
        own_msg_id: int | None = None, origin_session_id: int | None = None,
    ) -> tuple[str, str, int | None]:
        """Run the target agent's own orchestrator loop; returns (text, status, session_id)."""
        # House Party = full interactive grade across all agents (D-C4); normal
        # agent-to-agent traffic runs on the background tier. The profile owns
        # the actual model IDs either way (Rule 10).
        model = profile.allocate_model("user" if house_party else "agent")

        # The GROUP CHANNEL: recent network traffic, shared by the whole roster —
        # the dispatched agent joins the conversation instead of waking up with
        # amnesia. Its own incoming task is excluded (it follows as TASK below).
        channel = await channel_transcript(exclude_id=own_msg_id)
        channel_block = (
            "\n\nAGENT NETWORK CHANNEL — recent traffic between all agents "
            "(oldest first). Use it for context and continuity; do not repeat "
            f"work already answered here:\n{channel}"
        ) if channel else ""
        seed = (
            f"Inter-agent dispatch from {from_agent.upper()}. Complete "
            "the task below and reply with the result — your answer "
            f"goes back to {from_agent.upper()}, not to the owner, so "
            "skip greetings and pleasantries and lead with the "
            "substance. Be complete but compact."
            f"{channel_block}\n\n"
            f"TASK: {task}"
        )
        try:
            async with AsyncSessionLocal() as db:
                session = await self._session_manager.get_or_create(
                    db=db, user_id=user_id, triggered_by="agent",
                    model_used=model, agent_id=profile.agent_id,
                )
                if session.title is None:
                    session.title = session_title(from_agent, task)
                    await db.commit()

                # Persist FIRST, then read the history back — the order chat and
                # the trigger runner both use. The seed is stamped from its own
                # created_at rather than by hand, so the transcript the owner
                # opens is byte-identical to the prompt the model saw. (That
                # stamp is also how a dispatched agent knows what day it is: the
                # system prompt carries no clock, and an unstamped turn left
                # date-scoped tools querying a hallucinated window.)
                #
                # `text` in the meta block is what the BUBBLE shows: the task the
                # caller actually sent, not the routing preamble and the whole
                # agent-channel scrollback the model needs to read.
                await self._session_manager.save_message(db, session.id, "user", [
                    {"type": "text", "text": seed},
                    {
                        "type": "_speda_meta",
                        "trigger": dispatch_meta(from_agent, house_party),
                        "text": task,
                    },
                ])
                history = await self._session_manager.load_history(db, session.id)

                context = AgentContext(
                    agent_id=profile.agent_id,
                    user_id=user_id,
                    session_id=session.id,
                    request_id=request_id,
                    triggered_by="agent",
                    trigger_payload={"from_agent": from_agent, "task": task},
                    output_mode="silent",
                    model=model,
                    system_prompt="",
                    conversation_history=history,
                    db=db,
                    timezone=settings.owner_timezone,
                    extra={
                        "dispatch_depth": depth + 1,
                        # Carry the ROOM down the chain: anything this agent
                        # dispatches onward is still part of the conversation the
                        # owner is watching, so it must log against the same
                        # origin session instead of the agent's private one.
                        "room_session_id": origin_session_id,
                    },
                )

                chunks: list[str] = []
                errors: list[str] = []
                tools: list[dict] = []
                files: list[dict] = []
                chars = 0  # stamped onto each tool as afterChars, so a reloaded
                # transcript interleaves tool cards where they actually fired
                # (same contract as TurnRegistry._persist)
                try:
                    async for event in self._orchestrator.run(context):
                        if event.type == SSEEventType.CHUNK and isinstance(event.data, str):
                            chunks.append(event.data)
                            chars += len(event.data)
                        elif event.type == SSEEventType.ERROR:
                            errors.append(str(event.data))
                        elif event.type == SSEEventType.TOOL:
                            d = event.data if isinstance(event.data, dict) else {}
                            tools.append({
                                "id": d.get("id"), "name": d.get("name"),
                                "input": d.get("input"), "afterChars": chars,
                            })
                        elif event.type == SSEEventType.TOOL_RESULT:
                            d = event.data if isinstance(event.data, dict) else {}
                            for t in tools:
                                if t.get("id") == d.get("id"):
                                    t["result"] = d.get("result")
                                    break
                        elif event.type == SSEEventType.FILE:
                            files.append(event.data)
                except BaseException:
                    # Including cancellation: a run that did real work before it
                    # broke off must leave the work behind, not an empty session.
                    await self._save_reply(db, session.id, chunks, tools, files, request_id)
                    raise
                await self._save_reply(
                    db, session.id, chunks, tools, files, request_id, errors=errors,
                )

                text = "".join(chunks).strip()[:MAX_RESULT_CHARS]
                if text:
                    return text, "ok", session.id
                if errors:
                    return f"{profile.agent_id} failed: {'; '.join(errors)}", "error", session.id
                return f"{profile.agent_id} returned an empty response.", "error", session.id
        except Exception as e:  # noqa: BLE001 — dispatch must never raise into the caller's loop
            logger.error(
                "agent_dispatch_error",
                extra={"request_id": request_id, "to": profile.agent_id, "error": str(e)},
            )
            return f"Dispatch to {profile.agent_id} failed: {e}", "error", None

    async def _save_reply(
        self, db, session_id: int, chunks: list[str], tools: list[dict],
        files: list[dict], request_id: str, errors: list[str] | None = None,
    ) -> None:
        """Persist the dispatched agent's answer as a normal assistant turn.

        Mirrors TurnRegistry._persist, including its judgement call: a turn that
        ran tools counts as work even with no text, and dropping it would erase
        what the agent did from a transcript the owner can open. Best-effort —
        the dispatch's own result is already on its way back to the caller, so a
        persistence failure is logged, never raised."""
        full = "".join(chunks)
        if errors:
            # The reply reads as finished otherwise; the transcript has to show
            # it broke off, and this guarantees there is something to persist
            # even when nothing streamed before the failure.
            full += f"\n\n_[turn ended early — error: {'; '.join(errors)[:500]}]_"
        if not (full or tools or files):
            return
        content: list = [{"type": "text", "text": full}]
        if tools or files:
            content.append({"type": "_speda_meta", "tools": tools, "files": files})
        try:
            await self._session_manager.save_message(db, session_id, "assistant", content)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "dispatch_persist_failed",
                extra={"request_id": request_id, "session_id": session_id, "error": str(e)},
            )

    # ── External (WebSocket peer) path ───────────────────────────────────────

    async def _run_external(
        self, *, to_agent: str, from_agent: str, task: str, cwd: str | None = None,
    ) -> tuple[str, str]:
        """Dispatch to a standalone peer (Optimus) over its WebSocket connection."""
        if self._ws_manager is None or not self._ws_manager.is_connected(to_agent):
            return (
                f"'{to_agent}' is not a registered agent and no external peer by "
                "that name is connected. Do not retry until it comes online.",
                "offline",
            )
        # Which machine. A peer may be attached from several (the server and
        # the owner's PC) and is still one agent; the working directory picks
        # which one runs this task. A directory nobody covers is refused now,
        # not after EXTERNAL_CODING_TIMEOUT_S, and never by falling through to
        # a peer that would silently succeed elsewhere.
        decision = resolve(self._ws_manager.peers(to_agent), cwd)
        if not decision.ok:
            # "refused", NOT "error": an error means "the peer failed, try the
            # in-process profile", and falling back here would run the task on
            # the server after the owner asked for a directory on their PC —
            # the exact silent-success-elsewhere this refusal exists to stop.
            return decision.error, "refused"
        host = decision.host
        # Coding peers need room for multi-step tool loops; everything else
        # keeps the tight default so a dead peer can't stall its caller long.
        timeout = EXTERNAL_CODING_TIMEOUT_S if to_agent == "optimus" else EXTERNAL_TIMEOUT_S
        task_id = str(uuid.uuid4())
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending_external[task_id] = fut
        try:
            await self._ws_manager.send(to_agent, {
                "type": "task_dispatch",
                "task_id": task_id,
                "from": from_agent,
                "task": task,
                "cwd": cwd,
                "permission_mode": None,
            }, host)
            result, status = await asyncio.wait_for(fut, timeout)
            return str(result)[:MAX_RESULT_CHARS], status
        except asyncio.TimeoutError:
            return (
                f"{to_agent} did not answer within {int(timeout)}s. "
                "The task may still be running on the peer; do not blindly retry.",
                "timeout",
            )
        except Exception as e:  # noqa: BLE001
            return f"Dispatch to external peer {to_agent} failed: {e}", "error"
        finally:
            self._pending_external.pop(task_id, None)

    # ── Telemetry (best-effort — never fails a dispatch) ─────────────────────

    async def _log_start(
        self, *, request_id: str, from_agent: str, to_agent: str,
        kind: str, protocol: str, task: str, status: str = "running",
        origin_session_id: int | None = None,
    ) -> int | None:
        try:
            async with AsyncSessionLocal() as db:
                row = AgentMessage(
                    request_id=request_id, from_agent=from_agent, to_agent=to_agent,
                    kind=kind, protocol=protocol, task=task[:4000], status=status,
                    origin_session_id=origin_session_id,
                    created_at=datetime.utcnow(),
                )
                db.add(row)
                await db.commit()
                return row.id
        except Exception as e:  # noqa: BLE001
            logger.warning("agent_message_log_failed", extra={"error": str(e)})
            return None

    async def _log_finish(
        self, msg_id: int | None, *, status: str, result: str,
        session_id: int | None, duration_ms: int,
    ) -> None:
        if msg_id is None:
            return
        try:
            async with AsyncSessionLocal() as db:
                row = await db.get(AgentMessage, msg_id)
                if row is None:
                    return
                row.status = status
                row.result = result[:8000]
                row.session_id = session_id
                row.duration_ms = duration_ms
                await db.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("agent_message_log_failed", extra={"error": str(e)})
