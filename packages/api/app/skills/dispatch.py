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
            "Dispatches a task to another agent in the suite and returns that "
            "agent's answer to you within this turn. The target runs its own full "
            "reasoning loop with its own tools and domain expertise, so use this "
            "when a task clearly belongs to a specialist's domain or when you need "
            "several domains worked in parallel (emit multiple dispatch_agent calls "
            "in one turn — they run concurrently). Available agents: "
            f"{agent_lines}. Do NOT use it for anything you can do with your own "
            "tools in a comparable effort — a dispatch costs a full model run — and "
            "never dispatch to yourself. Set agent='all' to broadcast one task to "
            "every other agent at once; that requires the House Party Protocol to "
            "be engaged. Returns the target agent's final text (or a broadcast "
            "digest, one section per agent). When you use another agent's answer, "
            "tell the owner which agent ran, in one sentence."
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
                "working_directory": {
                    "type": "string",
                    "description": (
                        "Optional absolute path for CODING tasks dispatched to "
                        "Optimus — the directory on Optimus's own machine where the "
                        "work should happen (a repo or project folder). Omit for "
                        "non-coding tasks and for every other agent."
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
        if agent == "all":
            return await self._dispatcher.broadcast(
                from_agent=context.agent_id, task=task,
                user_id=context.user_id, request_id=context.request_id,
                depth=depth,
            )
        return await self._dispatcher.dispatch(
            from_agent=context.agent_id, to_agent=agent, task=task,
            user_id=context.user_id, request_id=context.request_id,
            depth=depth,
            cwd=(args.get("working_directory") or "").strip() or None,
        )


class AgentChannelSkill(Skill):
    name = "read_agent_channel"
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


class HousePartySkill(Skill):
    name = "house_party"
    description = (
        "Engages or stands down the House Party Protocol — the all-hands mode for "
        "extremely high-stakes situations, where SPEDA becomes mission commander, "
        "plans the objective, and dispatches the ENTIRE roster in parallel with "
        "every agent at full model grade and domain boundaries relaxed. This is a "
        "HEAVY, EXPENSIVE, still-PROTOTYPE capability: it burns full-grade model "
        "cost across all agents at once, so it is never the way to answer routine "
        "questions (the time, a lookup, a single-agent task). Engaging REQUIRES an "
        "authorization passphrase that only the owner holds — you do not know it "
        "and must never invent, guess, or reuse one. When the owner asks to engage: "
        "FIRST render the warning card by emitting a fenced code block with language "
        "`hpp-warning` (the UI renders it as a striking heavy/expensive/prototype "
        "authorization window; put a one-line objective inside the block if you have "
        "one) and ask them to speak the authorization passphrase; only once they give "
        "it in their message do you call this tool with that exact passphrase. Engage "
        "ONLY on the owner's explicit invocation (e.g. 'House Party Protocol', "
        "'assemble the agents', 'all hands on deck') — NEVER on your own judgement, "
        "never inferred from urgency, and a dispatched agent must never engage it. "
        "Standing down needs no passphrase — do it whenever the owner says the "
        "situation is resolved ('stand down', 'party's over'). State persists "
        "across restarts and transforms the owner's UI into the war room while "
        "active; this tool returns a confirmation of the new protocol state."
    )
    read_only = False
    input_schema = {
        "type": "object",
        "properties": {
            "engaged": {
                "type": "boolean",
                "description": "True to engage the protocol, False to stand down.",
            },
            "passphrase": {
                "type": "string",
                "description": (
                    "The owner's authorization passphrase, required to ENGAGE "
                    "(ignored when standing down). Pass the exact phrase the owner "
                    "spoke in their message this turn — never a guessed or "
                    "remembered value. Omit it and the engage is refused."
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

        engaged = bool(args.get("engaged", False))

        # Standing down is always safe and needs no passphrase.
        if not engaged:
            was = get_house_party()
            set_house_party(False)
            logger.info(
                "house_party_toggled_by_agent",
                extra={"request_id": context.request_id, "from": was, "to": False},
            )
            return (
                "House Party Protocol stood down. Inter-agent dispatch is back on "
                "the background tier, broadcast is disabled, and the war room closes."
            )

        # ── Engage: passphrase-gated ────────────────────────────────────────────
        # The protocol is heavy/expensive/prototype, so it only arms on the
        # owner's exact authorization passphrase. Constant-time compare; SPEDA
        # never learns the secret — it must relay what the owner spoke.
        import hmac

        supplied = str(args.get("passphrase") or "").strip()
        expected = (settings.house_party_passphrase or "").strip()
        if not supplied or not expected or not hmac.compare_digest(supplied, expected):
            logger.warning(
                "house_party_engage_denied",
                extra={
                    "request_id": context.request_id,
                    "reason": "missing_passphrase" if not supplied else "bad_passphrase",
                },
            )
            return (
                "REFUSED — House Party Protocol not engaged: "
                + ("no authorization passphrase was supplied."
                   if not supplied else "the authorization passphrase was incorrect.")
                + " Do NOT retry with a guessed value. Instead, present the owner the "
                "warning card by emitting a fenced ```hpp-warning code block (the UI "
                "renders it as a HEAVY/EXPENSIVE/PROTOTYPE authorization window that "
                "notes the protocol runs the entire roster at full model grade), and "
                "ask them to speak the exact authorization passphrase. Only call this "
                "tool again once the owner gives the passphrase in their next message."
            )

        was = get_house_party()
        set_house_party(True)
        logger.info(
            "house_party_toggled_by_agent",
            extra={"request_id": context.request_id, "from": was, "to": True, "authorized": True},
        )
        return (
            "House Party Protocol ENGAGED — authorization accepted. The full roster "
            "is at your command at full model grade; the owner's UI is switching to "
            "the war room. Now: state the objective, decompose it, and dispatch the "
            "agents in parallel this turn — then iterate on their results until the "
            "mission is done and debrief the owner."
        )
