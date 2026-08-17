"""
Inter-agent dispatch tools.

dispatch_agent — lets any agent hand a task to another agent in the suite and
get the result back in-turn. The heavy lifting lives in app/core/dispatch.py
(the orchestrator-routed primitive); this skill is just the tool surface.

house_party — engages/stands down the House Party Protocol (runtime flag).

Both are constructed with the AgentDispatcher instance at startup (main.py) —
no module-level globals (Rule 6), and the agent roster in the tool schema is
built from the ProfileRegistry, never hardcoded (Rule 10).
"""

import logging

from app.config import settings
from app.core.context import AgentContext
from app.core.runtime_state import get_house_party, set_house_party
from app.core.surface import DESKTOP_ONLY_NOTICE, is_desktop_surface
from app.skills.base import Skill

logger = logging.getLogger(__name__)


class DispatchAgentSkill(Skill):
    name = "dispatch_agent"
    read_only = False
    requires_network = True  # the dispatched agent runs its own LLM loop

    def __init__(self, dispatcher, roster: list[tuple[str, str]]) -> None:
        """roster: (agent_id, domain) pairs from the ProfileRegistry."""
        self._dispatcher = dispatcher
        agent_lines = "; ".join(f"'{a}' ({d})" for a, d in roster)
        self.description = (
            "Dispatches a task to another agent in the suite. By default the target "
            "runs its full reasoning loop and returns its answer to you WITHIN this "
            "turn (you wait). The target has its own tools and domain expertise, so "
            "use this when a task clearly belongs to a specialist or when you need "
            "several domains worked in parallel (emit multiple dispatch_agent calls "
            "in one turn — they run concurrently). Available agents: "
            f"{agent_lines}. Set background=true for long jobs (deep research, a "
            "coding job on Optimus, anything the owner shouldn't wait on): the "
            "dispatch keeps running after your turn ends, you get a ticket back "
            "immediately, and when it finishes YOU ARE WOKEN with the answer and "
            "deliver it to the owner then — so say it is running and that you will "
            "report back, never that he should ask you later. Do NOT use this for anything "
            "you can do yourself in comparable effort (a dispatch costs a full model "
            "run) and never dispatch to yourself. Set agent='all' to broadcast to "
            "every other agent (House Party Protocol only). Returns the target's "
            "final text, or a background ticket. Always tell the owner which agent "
            "ran (or was launched), in one sentence."
        )
        self.input_schema = {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "enum": [a for a, _ in roster] + ["all"],
                    "description": (
                        "Target agent_id, or 'all' to broadcast to every other "
                        "agent (House Party Protocol only)."
                    ),
                },
                "task": {
                    "type": "string",
                    "description": (
                        "The task, self-contained: the target agent sees NOTHING of "
                        "your conversation, so include every fact, constraint, and "
                        "expected output format it needs."
                    ),
                },
                "background": {
                    "type": "boolean",
                    "description": (
                        "Run the dispatch in the background: return a ticket now and "
                        "keep working after your turn ends. Use for long jobs the "
                        "owner shouldn't wait on; check results later with "
                        "dispatch_status. Default false (you wait for the answer)."
                    ),
                    "default": False,
                },
                "working_directory": {
                    "type": "string",
                    "description": (
                        "Optional absolute POSIX path for CODING tasks dispatched "
                        "to Optimus — a directory on the SERVER, where Optimus "
                        "runs. It is not the owner's computer: never pass a "
                        "Windows path they mention (C:\\Users\\…), and never "
                        "assume 'here' means their machine. Omit for non-coding "
                        "tasks and for every other agent."
                    ),
                },
            },
            "required": ["agent", "task"],
        }

    async def execute(self, args: dict, context: AgentContext) -> str:
        agent = (args.get("agent") or "").strip().lower()
        task = (args.get("task") or "").strip()
        if not agent or not task:
            return "Both 'agent' and 'task' are required."

        depth = int(context.extra.get("dispatch_depth", 0))
        background = bool(args.get("background", False))
        cwd = (args.get("working_directory") or "").strip() or None
        # No platform check here. Which paths are legal depends on the machines
        # currently attached — Optimus may be running on the server AND on the
        # owner's PC, where `C:\Users\…` is the correct answer rather than the
        # bug it used to be. AgentDispatcher._run_external resolves the path
        # against the live peer list and refuses by name if nothing covers it
        # (app/core/peer_routing.py), so a guard here could only be wrong in
        # one direction or the other.
        # The room this exchange belongs to: the chat session the owner is
        # watching. On a dispatched agent that is the room it inherited, not its
        # own private session — so a second-hop dispatch still shows up in the
        # same group chat (app/core/dispatch.py).
        room = context.extra.get("room_session_id") or context.session_id

        if agent == "all":
            return await self._dispatcher.broadcast(
                from_agent=context.agent_id, task=task,
                user_id=context.user_id, request_id=context.request_id,
                depth=depth, background=background, origin_session_id=room,
            )
        if background:
            return await self._dispatcher.spawn(
                from_agent=context.agent_id, to_agent=agent, task=task,
                user_id=context.user_id, request_id=context.request_id,
                depth=depth, cwd=cwd, origin_session_id=room,
            )
        return await self._dispatcher.dispatch(
            from_agent=context.agent_id, to_agent=agent, task=task,
            user_id=context.user_id, request_id=context.request_id,
            depth=depth, cwd=cwd, origin_session_id=room,
        )


class AgentChannelSkill(Skill):
    name = "read_agent_channel"
    # NOT deferred, deliberately (~193 tokens of prefix). Deferring it took its
    # call count to exactly ZERO for the six days after 2026-07-29 — it had been
    # used before. This is one of the tools an agent reaches for to CHECK what
    # actually happened rather than assert it, and a verification tool nobody can
    # find is worse than a slightly larger prefix.
    description = (
        "Reads the agent network's group channel — the shared conversation log of "
        "every inter-agent dispatch and reply across the whole suite, newest-first "
        "window rendered oldest-first like a chat scrollback. Use it when the owner "
        "asks what the agents have been discussing or working on, before dispatching "
        "a task that another agent may already have answered, or to pick up context "
        "from earlier network traffic. Do NOT use it to recall your own conversations "
        "with the owner — that is search_history / recall_conversations territory. "
        "Returns the formatted channel transcript, optionally filtered to exchanges "
        "involving one agent, or a note that the channel is empty."
    )
    read_only = True
    input_schema = {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "How many exchanges to show (default 20, max 60).",
                "default": 20,
            },
            "agent": {
                "type": "string",
                "description": "Optional agent_id — only exchanges involving this agent.",
            },
        },
        "required": [],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        from app.core.dispatch import channel_transcript

        limit = int(args.get("limit", 20) or 20)
        agent = (args.get("agent") or "").strip().lower() or None
        transcript = await channel_transcript(limit=limit, agent=agent)
        if not transcript:
            return "The agent network channel is empty — no inter-agent traffic yet."
        return f"AGENT NETWORK CHANNEL (oldest first):\n{transcript}"


class DispatchStatusSkill(Skill):
    name = "dispatch_status"
    # NOT deferred — same reason as read_agent_channel above. Zero calls in the
    # six days after deferral. It is how a background dispatch's real outcome is
    # retrieved, so losing it means the model answers "is X done?" from memory.
    description = (
        "Checks on background dispatches you launched with dispatch_agent "
        "(background=true) — the long-running jobs that keep working after your "
        "turn ends. A finished background dispatch wakes you with its answer on its "
        "own, so use this only when the owner asks 'is X done yet?' BEFORE it lands, "
        "or when you need a still-running job's state. Do NOT use it for a normal (blocking) "
        "dispatch — those already returned their answer in-turn — or to browse "
        "general inter-agent traffic (that is read_agent_channel). Pass a ticket "
        "'id' to check one dispatch, or omit it to list your recent dispatches with "
        "their state. Returns each dispatch's status (running / ok / error), how "
        "long it ran, and the result text once finished."
    )
    read_only = True
    input_schema = {
        "type": "object",
        "properties": {
            "id": {"type": "integer", "description": "A dispatch ticket id (from a background dispatch). Omit to list recent ones."},
        },
        "required": [],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        from sqlalchemy import select

        from app.database import AsyncSessionLocal
        from app.models.agent_message import AgentMessage

        ticket_id = args.get("id")
        async with AsyncSessionLocal() as db:
            if ticket_id is not None:
                row = await db.get(AgentMessage, int(ticket_id))
                if row is None or row.from_agent != context.agent_id:
                    return f"No dispatch #{ticket_id} that you launched was found."
                return _fmt_dispatch(row)
            # No id → this agent's recent dispatches, newest first. Legion
            # worker tickets are excluded — those belong to legion_status.
            stmt = (
                select(AgentMessage)
                .where(
                    AgentMessage.from_agent == context.agent_id,
                    AgentMessage.kind != "legion",
                )
                .order_by(AgentMessage.id.desc())
                .limit(10)
            )
            rows = list((await db.execute(stmt)).scalars().all())
        if not rows:
            return "You have not dispatched anything yet."
        return "Your recent dispatches:\n" + "\n".join(_fmt_dispatch(r, brief=True) for r in rows)


def _fmt_dispatch(row, brief: bool = False) -> str:
    from app.core.dispatch import MAX_RESULT_CHARS

    dur = f"{row.duration_ms}ms" if row.duration_ms is not None else "…"
    head = f"#{row.id} → {row.to_agent.upper()} [{row.status}] ({dur})"
    if row.status == "running":
        return f"{head}: still working" + ("" if brief else f"\n  task: {row.task[:200]}")
    if brief:
        preview = (row.result or "")[:120].replace("\n", " ")
        return f"{head}: {preview}"
    return f"{head}\n  task: {row.task[:300]}\n  result: {(row.result or '(empty)')[:MAX_RESULT_CHARS]}"


# How this tool's one required argument is actually read off the wire.
#
# `engaged` is a required boolean, but only Anthropic reliably fills it. Prod has
# observed `{}` (Gemini) and `{"action": "engage"}` (DeepSeek) on turns where the
# owner plainly asked to ENGAGE — non-Anthropic providers do not enforce
# `required`, and the owner has every agent pinned to one of them. Reading a
# missing `engaged` as False turned each of those calls into a silent STAND-DOWN,
# and the model, handed a stand-down confirmation, told the owner the opposite.
#
# So: understand the shapes the models really emit, and REFUSE when the call
# carries no intent at all. Never default. Guessing "engage" opens an
# authorization window the owner never asked for; guessing "stand down" is the
# bug this replaces. Only an explicit intent may move the flag.

_ENGAGE_WORDS = frozenset({
    "engage", "engaged", "engaging", "activate", "activated", "assemble",
    "enable", "enabled", "start", "on", "true", "yes", "1",
})
_STAND_DOWN_WORDS = frozenset({
    "stand down", "standdown", "disengage", "disengaged", "deactivate",
    "disable", "disabled", "stop", "end", "off", "false", "no", "0",
})
# Keys models have used in place of `engaged`. Order matters: the documented
# name wins when a call somehow carries several.
_ENGAGED_KEYS = ("engaged", "engage", "action", "state", "status", "mode", "enabled")


def _read_engaged(args: dict) -> bool | None:
    """The call's intent: True = engage, False = stand down, None = not stated.

    None is not a failure to parse — it means the model never expressed a
    direction, and the caller must refuse rather than pick one.
    """
    import re

    if not isinstance(args, dict):
        return None
    for key in _ENGAGED_KEYS:
        if key not in args:
            continue
        value = args[key]
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            word = re.sub(r"[\s_-]+", " ", value.strip().lower())
            if word in _ENGAGE_WORDS:
                return True
            if word in _STAND_DOWN_WORDS:
                return False
    return None


class HousePartySkill(Skill):
    name = "house_party"
    deferred = True
    search_keywords = "house party protocol all hands roster emergency all agents mobilize"
    description = (
        "Engages or stands down the House Party Protocol — the all-hands mode for "
        "extremely high-stakes situations, where SPEDA becomes mission commander, "
        "plans the objective, and dispatches the ENTIRE roster in parallel with "
        "every agent at full model grade and domain boundaries relaxed. This is a "
        "HEAVY, EXPENSIVE, still-PROTOTYPE capability: it burns full-grade model "
        "cost across all agents at once, so it is never the way to answer routine "
        "questions (the time, a lookup, a single-agent task). Engaging REQUIRES an "
        "authorization passphrase that only the owner holds. When the owner asks to "
        "engage: call this tool with engaged=true, NO passphrase, and an optional "
        "one-line `objective`. That opens a secure authorization WINDOW in the "
        "owner's app with a masked passphrase field — the owner types the "
        "passphrase THERE and the app engages the protocol itself. Never write a "
        "warning card, a code block, or a fenced block of your own, and never ask "
        "the owner to type the passphrase into the chat: you must not see or handle "
        "it. Make the call, tell the owner in one line that the authorization "
        "window is open, and stop. Only request authorization on the owner's explicit "
        "invocation (e.g. 'House Party Protocol', 'assemble the agents', 'all hands "
        "on deck') — NEVER on your own judgement, never inferred from urgency, and a "
        "dispatched agent must never trigger it. Standing down is the one action you "
        "DO perform with this tool (engaged=false, no passphrase) — do it whenever "
        "the owner says the situation is resolved ('stand down', \"party's over\"). "
        "State persists across restarts and transforms the owner's UI into the war "
        "room while active; this tool returns a confirmation of the new state."
    )
    read_only = False
    input_schema = {
        "type": "object",
        "properties": {
            "engaged": {
                "type": "boolean",
                "description": "True to engage the protocol, False to stand down.",
            },
            "objective": {
                "type": "string",
                "description": (
                    "One line naming the mission, shown in the authorization "
                    "window so the owner sees what they are authorizing. Omit it "
                    "when the owner has not said what the all-hands run is for."
                ),
            },
            "passphrase": {
                "type": "string",
                "description": (
                    "Leave this OUT. It exists only for surfaces with no "
                    "authorization window (Telegram), where the owner may speak "
                    "the passphrase in their message — pass the exact phrase they "
                    "spoke this turn, never a guessed or remembered value. On the "
                    "app, omitting it is correct: the owner types it in the window."
                ),
            },
        },
        "required": ["engaged"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        # Hard guard: only a turn the OWNER initiated may flip the protocol —
        # dispatched agents and automated triggers cannot (prompt rules aside).
        if context.triggered_by != "user":
            return (
                "Refused: the House Party Protocol can only be engaged or stood "
                "down in a conversation with the owner, never from a dispatched "
                "task or automation."
            )

        # An unstated direction moves nothing. See _read_engaged: the old
        # `args.get("engaged", False)` silently stood the protocol down whenever a
        # provider dropped the boolean, which is exactly how an engage request
        # became a stand-down the model then misreported as success.
        engaged = _read_engaged(args)
        if engaged is None:
            logger.warning(
                "house_party_intent_missing",
                # Keys only — a call may carry the passphrase, which never gets logged.
                extra={
                    "request_id": context.request_id,
                    "keys": sorted(args)[:8] if isinstance(args, dict) else "non-dict",
                },
            )
            return (
                "REFUSED — nothing changed, and the protocol's state is EXACTLY as it "
                "was: this call did not say whether to engage or stand down. Do not "
                "tell the owner anything about the protocol's state yet. Call "
                "house_party again using the parameter name `engaged` literally: "
                "engaged=true to open the owner's authorization window, engaged=false "
                "to stand the protocol down. No other parameter name works."
            )

        # Standing down is always safe and needs no passphrase.
        if not engaged:
            was = get_house_party()
            set_house_party(False)
            logger.info(
                "house_party_toggled_by_agent",
                extra={"request_id": context.request_id, "from": was, "to": False},
            )
            if not was:
                # It was already down. Say so precisely — reporting this as a
                # completed stand-down is what let a model read "stood down" and
                # announce "engaged" to the owner.
                return (
                    "No change: the House Party Protocol was ALREADY stood down and "
                    "remains so. It is NOT engaged. Tell the owner exactly that — if "
                    "they asked you to engage it, you called this tool with the wrong "
                    "direction; call it again with engaged=true."
                )
            return (
                "House Party Protocol stood down. Inter-agent dispatch is back on "
                "the background tier, broadcast is disabled, and the war room closes."
            )

        # ── Engage: desktop only ────────────────────────────────────────────────
        # Checked BEFORE the passphrase so a phone request never opens the
        # authorization window at all. The war room — the roster staged, the
        # live transcript, the colour parade — is built by the desktop client
        # and nowhere else; engaging from the phone would run every agent at
        # full interactive grade with nothing on screen to show for it.
        if not is_desktop_surface(context.extra.get("client_platform")):
            logger.info(
                "house_party_engage_denied_surface",
                extra={
                    "request_id": context.request_id,
                    "platform": context.extra.get("client_platform") or "unknown",
                },
            )
            return (
                f"REFUSED — the protocol is NOT engaged and nothing changed. "
                f"{DESKTOP_ONLY_NOTICE} Tell the owner this in your own words, and "
                f"do not call this tool again for this request: retrying from the "
                f"same client will be refused the same way. If they want the "
                f"protocol, they need to ask from the desktop app."
            )

        # ── Engage: passphrase-gated ────────────────────────────────────────────
        # The protocol is heavy/expensive/prototype, so it only arms on the
        # owner's exact authorization passphrase. Constant-time compare; SPEDA
        # never learns the secret.
        import hmac

        supplied = str(args.get("passphrase") or "").strip()
        expected = (settings.house_party_passphrase or "").strip()

        # No passphrase — the normal path. Raise an authorization ASK: the
        # orchestrator turns this into a `house_party_auth` SSE event and the
        # app opens its own window with a masked field, which engages the
        # protocol directly via POST /agents/house-party. Nothing about the
        # secret passes through the model or the transcript.
        if not supplied:
            # Telegram has no authorization window to open, so there the owner
            # does have to speak the phrase — say so instead of promising a
            # window that will never appear.
            if context.trigger_payload.get("channel") == "telegram":
                return (
                    "House Party Protocol not engaged: this channel has no "
                    "authorization window. Ask the owner to reply with the exact "
                    "authorization passphrase, then call this tool again passing "
                    "it verbatim. Never guess it."
                )

            objective = str(args.get("objective") or "").strip()[:180]
            context.extra["house_party_auth"] = {"objective": objective}
            logger.info(
                "house_party_auth_requested",
                extra={"request_id": context.request_id, "objective": objective},
            )
            return (
                "Authorization window opened on the owner's app — it is asking them "
                "for the House Party passphrase now. Tell the owner in ONE line that "
                "the window is open and then STOP: do not call this tool again, do "
                "not ask for the passphrase in chat, and do not act as if the "
                "protocol were engaged. The app engages it itself once they "
                "authorize; you will see the war room open on the next turn."
            )

        if not expected or not hmac.compare_digest(supplied, expected):
            logger.warning(
                "house_party_engage_denied",
                extra={"request_id": context.request_id, "reason": "bad_passphrase"},
            )
            return (
                "REFUSED — House Party Protocol not engaged: the authorization "
                "passphrase was incorrect. Do NOT retry with a guessed value. Call "
                "this tool once more with engaged=true and NO passphrase to reopen "
                "the authorization window, and let the owner authorize there."
            )

        was = get_house_party()
        set_house_party(True)
        logger.info(
            "house_party_toggled_by_agent",
            extra={"request_id": context.request_id, "from": was, "to": True, "authorized": True},
        )
        return (
            "House Party Protocol ENGAGED — authorization accepted. The full roster "
            "is up at full model grade; the owner's UI is switching to "
            "the war room. Now: state the objective, decompose it, and dispatch the "
            "agents in parallel this turn — then iterate on their results until the "
            "mission is done and debrief the owner."
        )
