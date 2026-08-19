import asyncio
import logging
import time
from typing import AsyncGenerator

from app.core.context import AgentContext
from app.core.registry import CapabilityRegistry
from app.models.tool_call import ToolCall
from app.profiles.registry import ProfileRegistry
from app.schemas.sse import SSEEvent, SSEEventType
from app.services.llm_client import LLMClient, blocks_to_dicts
from app.skills.memory import MemoryRecallCache, recall_for_context, recall_sessions_for_context

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 30  # Safety guard — Rule 4a

# Cap on what a tool_calls row stores of a result — generous enough for real
# debugging (unlike the 1500-char SSE preview, which only has to look right in
# the UI), bounded so one tool that returns a page of HTML doesn't make this
# table the thing that fills the disk.
_TOOL_RESULT_STORE_CHARS = 4000


async def _timed(tool_name: str, args: dict, context: AgentContext, registry: CapabilityRegistry):
    """Run one tool call and report how long it took, for the tool_calls audit
    row — `asyncio.gather` alone loses this once several calls run concurrently
    and finish at different times."""
    start = time.monotonic()
    result = await registry.execute(tool_name, args, context)
    return result, int((time.monotonic() - start) * 1000)


class AgentOrchestrator:
    """
    Owns the agentic loop and the system prompt.
    Neither lives anywhere else (CLAUDE.md Rules 1, 2, 4).

    Stateless with respect to identity: it holds the ProfileRegistry, not one
    profile, and resolves the agent per request from context.agent_id. The same
    loop serves every in-process agent (Speda + the five Superior Six).

    Router contract: call run(context) and stream the SSEEvents.
    Zero logic in the router beyond that.
    """

    def __init__(
        self,
        registry: CapabilityRegistry,
        client: LLMClient,
        profiles: ProfileRegistry,
        memory_cache: MemoryRecallCache,
    ) -> None:
        self._registry = registry
        self._client = client
        self._profiles = profiles
        self._memory_cache = memory_cache

    def build_system_prompt(self, context: AgentContext) -> str:
        """
        Build the full system prompt from the agent's profile template + runtime
        context vars. The profile is resolved per request from context.agent_id
        (Rule 2: prompt construction stays here; it just selects which profile to
        build from). Only called here — never in a router, never in a service.

        Deliberately NO time-derived vars: a clock anywhere in the system prompt
        changes the request prefix every minute, which silently invalidates
        prompt caching on every provider (Anthropic explicit, OpenAI/Gemini
        implicit, Ollama KV). Current time reaches the model via the timestamp
        stamped onto each user message (SessionManager.stamp_user_content).
        """
        profile = self._profiles.require(context.agent_id)
        return profile.build_system_prompt(
            {
                "timezone": context.timezone,
                "model": context.model,
            }
        )

    async def run(self, context: AgentContext) -> AsyncGenerator[SSEEvent, None]:
        """
        The agentic loop. Yields SSEEvents. The router streams them — never processes them.

        Stop reason handling (CLAUDE.md Rule 4):
          end_turn   → done, break
          tool_use   → execute tools, continue
          max_tokens → append continuation prompt, continue
          pause_turn → server tool loop limit hit, continue
        """
        log = logger.getChild("run")

        # Build the system prompt as TWO fully-cacheable blocks. The ENTIRE
        # system is stable now — no clock anywhere in the prefix:
        #
        #   1. stable_core   — identity + policies + tool guidance + per-model
        #                      addenda. Stable per model (and caches are
        #                      model-scoped anyway) → cached (biggest block).
        #   2. memory_block  — owner/current/dossier/history + size-free listing.
        #                      Changes at most ~daily → cached.
        #
        # Current time lives in per-message timestamps (stamped from each
        # message's DB created_at), AFTER the cached prefix and byte-stable
        # across turns. The previous design kept a minute-precision clock in an
        # uncached system tail — that tail sat in front of the conversation, so
        # the conversation cache entry was rewritten at the 1h-TTL 2x price on
        # EVERY turn and read ~never. Worse than no caching at all.
        stable_core = self.build_system_prompt(context)

        # Per-agent tool scoping: the profile's declared allowlist (None = the
        # full registry, e.g. Speda the orchestrator) governs what this agent can
        # see and load. Resolved once here and stored on the context so the
        # toolset catalog, the tool list, and Legion workers all share one scope.
        profile = self._profiles.require(context.agent_id)
        allowlist = (
            set(profile.tool_allowlist) if profile.tool_allowlist is not None else None
        )
        context.extra["tool_allowlist"] = allowlist

        # Per-agent document branding — the accent the generate_document skill
        # derives its PDF/DOCX/PPTX palette from. Profile-owned identity (Rule 10),
        # threaded to the skill via the context exactly like the allowlist above.
        context.extra["doc_accent"] = profile.doc_theme.accent

        # House Party Protocol — high-stakes all-hands mode (owner-engaged only).
        # Speda becomes mission commander; every other agent becomes an operative
        # that accepts tasks outside its domain. Engaged/stood down via the
        # house_party tool on the owner's explicit invocation.
        from app.core.runtime_state import get_house_party
        if get_house_party():
            if profile.house_party_commander:
                stable_core += (
                    "\n\n## HOUSE PARTY PROTOCOL — ACTIVE\n\n"
                    "The owner has engaged the all-hands protocol: the situation is "
                    "high-stakes and the entire agent roster is at your command. You "
                    "are the MISSION COMMANDER. For the owner's objective:\n"
                    "1. PLAN first — decompose the objective into concrete workstreams.\n"
                    "2. DISPATCH in parallel — one tailored dispatch_agent call per "
                    "agent in the same turn (prefer individually scoped tasks over a "
                    "broadcast; use agent='all' only when everyone genuinely needs "
                    "the identical brief). Assign by specialization where it fits, "
                    "but ANY agent may take ANY task — domain is a preference here, "
                    "not a rule. Every agent runs at full model grade.\n"
                    "3. ITERATE — when results return, dispatch follow-up waves until "
                    "the objective is genuinely done. Do not stop at one round if the "
                    "mission needs more.\n"
                    "4. DEBRIEF — synthesize everything into one decisive answer for "
                    "the owner: what was done, by whom, what it means, what's next.\n\n"
                    "### This is a GROUP CHAT\n"
                    "The owner is in the room with the whole roster. Every task you "
                    "dispatch and every agent's reply is rendered in their transcript "
                    "as that agent's own message, live, the moment it lands — they "
                    "read the agents directly, not a summary of them. So:\n"
                    "- Write each dispatch as a message TO that agent, in plain "
                    "language the owner can follow. It is a public brief, not an "
                    "internal payload.\n"
                    "- Never re-narrate or repeat an agent's answer back to the owner "
                    "— they already read it. Add only what is yours: the decision, "
                    "the conflict between two agents, the next move.\n"
                    "- Keep your own messages short, like someone talking in a group "
                    "chat. The long-form substance comes from the agents.\n"
                    "- When the owner addresses an agent directly (\"@centurion …\"), "
                    "dispatch that request to that agent essentially verbatim and let "
                    "their answer stand. Do not answer for them.\n\n"
                    "Stand the protocol down (house_party tool) when the owner says "
                    "the situation is resolved."
                )
            else:
                stable_core += (
                    "\n\n## HOUSE PARTY PROTOCOL — ACTIVE\n\n"
                    "The owner has engaged the all-hands protocol. You are an "
                    "OPERATIVE on a high-stakes mission: tasks dispatched to you may "
                    "fall outside your usual domain — take them anyway and deliver "
                    "your best work; specialization is a preference here, not a "
                    "boundary. Check the network channel in your briefing so you "
                    "build on the other agents' results instead of duplicating them.\n"
                    "You are speaking in a GROUP CHAT: your reply is shown to the "
                    "owner as your own message, under your own name, next to every "
                    "other agent's. Write it for them to read — lead with the "
                    "substance, no preamble, no restating the brief, no sign-off. "
                    "Short and concrete beats long and thorough here."
                )
        elif profile.house_party_commander:
            # State the OFF state explicitly. Without this line the model had no
            # grounding to contradict itself, and prod showed it announcing
            # "House Party Protocol engaged. All six agents standing by" on four
            # consecutive turns without ever calling the tool — the flag was
            # false throughout. Costs ~70 tokens on a prefix that only changes
            # when the flag does (rare), which is cheap for a claim the owner
            # would otherwise have no way to catch.
            stable_core += (
                "\n\n## HOUSE PARTY PROTOCOL — NOT ENGAGED\n\n"
                "The protocol is currently STOOD DOWN. This is the live state, not a "
                "default: whatever an earlier message in this conversation says, it is "
                "off right now. Never tell the owner it is engaged, and never speak as "
                "though the roster is assembled or standing by, unless THIS prompt says "
                "ACTIVE. Engaging it is not something you can assert — it happens only "
                "when you call the `house_party` tool with engaged=true and the owner "
                "then authorizes it in their app. If they ask you to engage it: call "
                "that tool (search for it by name if it is not in your tools array), "
                "report exactly what the tool returned, and if the call was refused say "
                "so plainly rather than claiming success."
            )

        # Budget mode — hard frugality directive (runtime-toggleable, persistent).
        from app.core.runtime_state import get_budget_mode
        if get_budget_mode():
            stable_core += (
                "\n\n## BUDGET MODE — ACTIVE\n\n"
                "The owner is on a strict budget. Enforce this every turn:\n"
                "- Keep answers SHORT — the minimum that fully answers the question. "
                "A few sentences or bullets. No multi-section reports, no scenario "
                "tables, unless the owner explicitly says 'deep dive' / 'full briefing'.\n"
                "- Run as FEW web searches as possible (ideally 1, at most 2-3).\n"
                "- The Legion is disabled. Do all work yourself in this turn.\n"
                "- If a request truly warrants depth, give a short answer first and "
                "ask whether to expand — never assume."
            )

        memory_block = ""
        if context.db is not None:
            try:
                memory_block = await recall_for_context(
                    context.user_id, context.db, context.agent_id, cache=self._memory_cache
                ) or ""
                if memory_block:
                    logger.info(
                        "memory_context_injected",
                        extra={"request_id": context.request_id},
                    )
            except Exception as exc:
                # Memory recall must never break a chat request
                logger.warning(
                    "memory_recall_failed",
                    extra={"request_id": context.request_id, "error": str(exc)},
                )

        # Episodic recall — recaps of the owner's recent PAST sessions, so a
        # brand-new session knows what the last conversations were about.
        # Scope is profile-owned (Rule 10): specialists see their own sessions,
        # the orchestrator profile sees every agent's.
        episodic_block = ""
        if context.db is not None:
            try:
                episodic_block = await recall_sessions_for_context(
                    context.user_id,
                    context.db,
                    context.agent_id,
                    context.session_id,
                    cache=self._memory_cache,
                    scope=profile.episodic_recall_scope,
                ) or ""
                if episodic_block:
                    logger.info(
                        "episodic_context_injected",
                        extra={"request_id": context.request_id},
                    )
            except Exception as exc:
                # Episodic recall must never break a chat request either
                logger.warning(
                    "episodic_recall_failed",
                    extra={"request_id": context.request_id, "error": str(exc)},
                )

        # Time protocol — replaces the old volatile "## Now" tail. Stable text;
        # the actual clock rides on the user messages. Model line is stable per
        # model, and provider caches are model-scoped anyway.
        stable_core += (
            "\n\n## Time\n\n"
            "Every user message is prefixed with its timestamp in the owner's "
            "local timezone, formatted [Ddd YYYY-MM-DD HH:MM TZ] — weekday, date, "
            "24-hour clock, then the zone abbreviation or UTC offset. The newest "
            "user message's stamp IS the current date and time — trust it over any "
            "internal sense of time, and speak in this local time unless asked "
            "otherwise.\n"
            "The weekday in that stamp is authoritative. NEVER compute a day of "
            "the week from a date yourself — read it off the stamp, and derive "
            "'tomorrow', 'this weekend', 'next Monday' and every other relative "
            "day by counting forward from it. If you are about to name a weekday "
            "for a date that is not in a stamp (a calendar entry, a deadline), "
            "count the days from the stamped one rather than recalling it.\n"
            f"Owner timezone: {context.timezone}\n"
            f"Active model: {context.model}"
        )

        # ── Automated-run discipline ────────────────────────────────────────
        # A trigger fires with no human in the loop. Weaker non-Anthropic models
        # (Speda runs on open models in prod) will happily write a plausible
        # briefing WITHOUT calling any tool if left to their own devices — the
        # daily-brief "pure hallucination" bug. This standing directive is
        # model-agnostic (the ollama block below is greeting-discipline, not
        # this) and forces execute-over-narrate + a hard no-fabrication rule.
        # Held OUT of stable_core deliberately. Appending it there forked the
        # cached prefix in two: every n8n turn and every chat turn built a
        # different ~30k system block, so neither could ever read the other's
        # cache entry and the automated side — which runs unattended, on a
        # schedule, and is the larger half of the token spend — paid a cold
        # prefix every single time. As a trailing block it sits AFTER the two
        # cached breakpoints, so both trigger sources now share one identical
        # cached prefix and only these few hundred tokens differ.
        automated_block = ""
        if context.triggered_by != "user":
            automated_block = (
                "## AUTOMATED RUN — EXECUTE, DON'T NARRATE\n\n"
                "This turn was fired by an automation, not a person. No one is "
                "waiting to answer questions and there is nothing to preview. Carry "
                "out the requested workflow end to end with REAL tool calls, then "
                "report only what actually happened.\n"
                "- Get every fact by CALLING the relevant tool. Load a toolset with "
                "use_toolset first when the tool isn't available yet (Gmail, "
                "Calendar, Notion). Do not answer from memory or assumption when a "
                "tool can give you the real value.\n"
                "- NEVER fabricate results. If a tool errors or returns nothing, say "
                "that in the output. An honest 'no new mail' or 'server metrics "
                "unavailable' is correct; an invented summary or made-up numbers is "
                "the worst possible outcome and defeats the entire automation.\n"
                "- Keep the final message concise and concrete, led by what actually "
                "happened."
            )

        # ── Dead Zone Protocol ──────────────────────────────────────────────
        # Offline mode: only offline-capable tools are exposed, and the model
        # is told the uplink is gone. Outside the dead zone, every provider —
        # including Ollama in dev — gets the full online toolset.
        dead_zone = await self._registry.dead_zone_active()
        provider = context.model.partition(":")[0] if ":" in context.model else "anthropic"

        if provider == "ollama":
            # Local models need firmer tool discipline: they call web search to
            # answer greetings and invent tool names. Stable per model → cached.
            stable_core += (
                "\n\n## Tool discipline\n\n"
                "Call a tool ONLY when the task genuinely requires it — live "
                "data, the user's files/memory, or an explicit action. Never "
                "call tools for greetings, small talk, or anything you already "
                "know. Only the tools in the tools list exist; never invent a "
                "tool name. When no tool fits, answer directly."
            )

        if dead_zone:
            stable_core += (
                "\n\n## DEAD ZONE PROTOCOL — ACTIVE\n\n"
                "No uplink. You are running on local compute only. Online "
                "capabilities (web search, mail, calendar, the Legion) are "
                "unavailable and have been removed from your tools. Work from "
                "local knowledge, memory and files; be direct about what cannot "
                "be done until the link is restored."
            )

        # Progressive tool disclosure. Deferred tools appear here by NAME ONLY;
        # `tool_search` turns a name into a callable tool when a task needs one.
        # Names cost a few tokens each against the hundreds a full Rule 11
        # description costs, so the model keeps sight of its whole capability
        # surface without the prefix carrying every schema on every iteration.
        # Pointless in a dead zone — the deferred set is remote either way.
        # NOT scoped by active_servers/loaded_tools on purpose — this text sits
        # in the `_cache`-flagged block below, so it must be identical every
        # turn of the session. See tool_index()'s docstring.
        index = "" if dead_zone else self._registry.tool_index(
            allowlist=allowlist,
            agent_id=context.agent_id,
        )
        if index:
            stable_core = f"{stable_core}\n\n{index}"

        # Structured system blocks. `_cache: True` marks the block for an ephemeral
        # cache breakpoint; the marker is stripped before the request is sent.
        system_blocks: list[dict] = [{"type": "text", "text": stable_core, "_cache": True}]
        if memory_block:
            system_blocks.append({"type": "text", "text": memory_block, "_cache": True})
        # Episodic block is deliberately NOT `_cache`-flagged: all four Anthropic
        # cache breakpoints are already spent (tools + the two blocks above + the
        # conversation tail). It doesn't need its own breakpoint — it is frozen
        # per session (see MemoryRecallCache in skills/memory.py), so the 5m
        # conversation breakpoint caches it as part of the stable prefix.
        if episodic_block:
            system_blocks.append({"type": "text", "text": episodic_block})
        # Trailing, uncached, and last on purpose — see the note where it is
        # built. Everything above this line is byte-identical for a chat turn
        # and an n8n turn on the same agent.
        if automated_block:
            system_blocks.append({"type": "text", "text": automated_block})

        # Keep a plain-string copy for any downstream logging/inspection.
        context.system_prompt = stable_core

        # Toolsets loaded this turn (grows when use_toolset is called).
        context.extra.setdefault("active_servers", set())
        # Deferred tools resolved by tool_search. Seeded from the session so a
        # tool found on turn 1 is still there on turn 9 without another search.
        context.extra.setdefault("loaded_tools", set())
        # The search skill resolves against the registry, so hand it the one the
        # orchestrator was built with rather than letting it reach for a global
        # (Rule 6 — no module-level state).
        context.extra["registry"] = self._registry

        # Anthropic resolves deferred tools with its own server-side tool-search;
        # every other provider has no such thing, so the registry withholds them
        # and our tool_search skill appends them. Same behaviour either way.
        defer_loading = provider == "anthropic"

        messages = list(context.conversation_history)
        tools = self._registry.list_tools(
            context.extra["active_servers"], offline_only=dead_zone,
            allowlist=allowlist, agent_id=context.agent_id,
            loaded_tools=context.extra["loaded_tools"], defer_loading=defer_loading,
        )
        iterations = 0
        produced_text = False  # any text streamed yet this turn (for paragraph breaks)

        # `trigger` rides the START event so a client attaching to a turn it did
        # not send can label it the moment it appears — a background job
        # reporting back renders the same folded card live as it does on reload,
        # where it comes off the persisted seed instead. Absent for ordinary
        # chat turns, which the client sent and already knows the origin of.
        yield SSEEvent(
            type=SSEEventType.START,
            data={
                "tools_available": len(tools),
                **({"trigger": tm} if (tm := context.extra.get("trigger_meta")) else {}),
            },
            session_id=context.session_id,
            request_id=context.request_id,
        )

        while True:
            # ── Safety guard (Rule 4a) ──────────────────────────────────────
            if iterations >= MAX_TOOL_ITERATIONS:
                log.error(
                    "safety_guard_triggered",
                    extra={
                        "request_id": context.request_id,
                        "iterations": iterations,
                    },
                )
                yield SSEEvent(
                    type=SSEEventType.ERROR,
                    data=f"Safety guard: maximum {MAX_TOOL_ITERATIONS} tool iterations reached.",
                    session_id=context.session_id,
                    request_id=context.request_id,
                )
                return

            log.info(
                "claude_call",
                extra={
                    "request_id": context.request_id,
                    "model": context.model,
                    "messages": len(messages),
                    "iteration": iterations,
                },
            )

            # ── Call Claude (streaming) ─────────────────────────────────────
            # Stream text deltas to the client in real time. `text_stream` yields
            # only text-block deltas as the model produces them; tool_use blocks
            # are read from the final assembled message afterward.
            async with self._client.stream_message(
                model=context.model,
                system=system_blocks,
                messages=messages,
                tools=tools,
                max_tokens=8096,
                # Cache-routing key for providers that expose one (OpenAI). Keyed
                # by agent+session so every iteration of every turn in one
                # conversation lands on the same cache entry — the prefix they
                # share is exactly the ~20k the agent re-sends each lap. Ignored
                # by every other provider.
                cache_key=f"{context.agent_id}-{context.session_id}",
            ) as stream:
                first_delta = True
                async for delta in stream.text_stream:
                    if not delta:
                        continue
                    # When a new text segment begins after earlier text in the same
                    # turn (i.e. resuming after a tool call), open a fresh paragraph
                    # so "Let me check.<tool>Done." doesn't render glued together.
                    if first_delta:
                        if produced_text:
                            yield SSEEvent(
                                type=SSEEventType.CHUNK, data="\n\n",
                                session_id=context.session_id, request_id=context.request_id,
                            )
                        first_delta = False
                    yield SSEEvent(
                        type=SSEEventType.CHUNK,
                        data=delta,
                        session_id=context.session_id,
                        request_id=context.request_id,
                    )
                    produced_text = True
                response = await stream.get_final_message()

            # Observability: how much of the input prefix was served from cache.
            usage = getattr(response, "usage", None)
            if usage is not None:
                cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
                cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
                log.info(
                    "prompt_cache",
                    extra={
                        "request_id": context.request_id,
                        "cache_read": cache_read,
                        "cache_write": cache_write,
                        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
                    },
                )
                # Running cost of THIS turn, summed over every iteration of the
                # loop. A tool-using turn re-sends the whole prompt each time
                # round, and each of those sends is billed, so the sum — not the
                # last iteration — is what the turn actually cost.
                #
                # `input` is what the model READ (uncached + cached), which is
                # the context-size figure the UI shows. `billable_input` is what
                # was charged at FULL rate — the number that actually moves the
                # invoice, and the only one that tells you whether caching is
                # working. Every provider now reports input_tokens EXCLUSIVE of
                # the cached prefix (see _usage_from), so the split is real on
                # all of them rather than Anthropic-only.
                spend = context.extra.setdefault(
                    "token_usage",
                    {"input": 0, "output": 0, "billable_input": 0,
                     "cache_read": 0, "cache_write": 0},
                )
                uncached = getattr(usage, "input_tokens", 0) or 0
                spend["input"] += uncached + cache_read + cache_write
                spend["billable_input"] += uncached
                spend["cache_read"] += cache_read
                spend["cache_write"] += cache_write
                spend["output"] += getattr(usage, "output_tokens", 0) or 0

            stop_reason = response.stop_reason

            # Convert content blocks to serialisable dicts for message history
            assistant_content = blocks_to_dicts(response.content)
            messages.append({"role": "assistant", "content": assistant_content})

            # ── end_turn ────────────────────────────────────────────────────
            # Text was already streamed above — nothing left to emit, just finish.
            if stop_reason == "end_turn":
                break

            # ── tool_use ────────────────────────────────────────────────────
            # Any preamble text Claude produced was already streamed above.
            elif stop_reason == "tool_use":
                iterations += 1
                tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

                # 1. Stream all the TOOL start events to the frontend immediately.
                #    Include the tool INPUT so the UI can show WHAT it did
                #    (memory content added, search query, command run, …).
                for tool_block in tool_use_blocks:
                    yield SSEEvent(
                        type=SSEEventType.TOOL,
                        data={"name": tool_block.name, "id": tool_block.id, "input": tool_block.input},
                        session_id=context.session_id,
                        request_id=context.request_id,
                    )
                    log.info(
                        "tool_call",
                        extra={
                            "request_id": context.request_id,
                            "tool": tool_block.name,
                            "tool_id": tool_block.id,
                        },
                    )

                # 2. Execute all tools in parallel, each timed individually for
                #    the tool_calls audit row persisted below.
                exec_tasks = [
                    _timed(block.name, block.input, context, self._registry)
                    for block in tool_use_blocks
                ]
                timed_results = await asyncio.gather(*exec_tasks)
                results = [r for r, _ in timed_results]

                # 2b. Emit each tool's RESULT (truncated) so the UI can show what
                #     came back when the user expands the tool disclosure.
                for block, res in zip(tool_use_blocks, results):
                    preview = res if isinstance(res, str) else str(res)
                    yield SSEEvent(
                        type=SSEEventType.TOOL_RESULT,
                        data={"id": block.id, "result": preview[:1500]},
                        session_id=context.session_id,
                        request_id=context.request_id,
                    )

                # 2c. Persist the audit row per call — tool_calls has carried the
                #     schema for this since day one but nothing ever wrote to it,
                #     so a cost or behaviour investigation had only the tool NAME
                #     from the log line above, never its input, result, timing or
                #     failure. Best-effort: a DB hiccup here must never break the
                #     turn the way a lost tool result would.
                if context.db is not None:
                    try:
                        for block, (res, duration_ms) in zip(tool_use_blocks, timed_results):
                            stored = (res if isinstance(res, str) else str(res))[:_TOOL_RESULT_STORE_CHARS]
                            context.db.add(ToolCall(
                                session_id=context.session_id,
                                request_id=context.request_id,
                                tool_name=block.name,
                                tool_input=block.input,
                                tool_result=stored,
                                duration_ms=duration_ms,
                                error=stored if stored.startswith("Error") else None,
                            ))
                        await context.db.commit()
                    except Exception as exc:
                        logger.warning(
                            "tool_call_persist_failed",
                            extra={"request_id": context.request_id, "error": str(exc)},
                        )
                        await context.db.rollback()

                # 2c. The house_party tool asks the owner to authorize by opening
                #     the app's own passphrase window. Emit it the moment the tool
                #     returns (not at end-of-turn like `file`) so the window is up
                #     while the model is still writing its one-line heads-up.
                auth_ask = context.extra.pop("house_party_auth", None)
                if auth_ask is not None:
                    yield SSEEvent(
                        type=SSEEventType.HOUSE_PARTY_AUTH,
                        data=auth_ask,
                        session_id=context.session_id,
                        request_id=context.request_id,
                    )

                # 2d. Same for the Lockdown Protocol's containment authorization.
                lock_ask = context.extra.pop("lockdown_auth", None)
                if lock_ask is not None:
                    yield SSEEvent(
                        type=SSEEventType.LOCKDOWN_AUTH,
                        data=lock_ask,
                        session_id=context.session_id,
                        request_id=context.request_id,
                    )

                # 3. Zip the results back to their respective tool blocks
                tool_results = [
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": res,
                    }
                    for block, res in zip(tool_use_blocks, results)
                ]

                messages.append({"role": "user", "content": tool_results})

                # A tool_search (or use_toolset) call may have resolved new
                # tools — rebuild so they're callable on the next iteration.
                # Resolved tools are APPENDED after the core set by list_tools,
                # never spliced into it, so the prefix the provider cached on the
                # previous iteration is still a prefix of this one.
                tools = self._registry.list_tools(
                    context.extra["active_servers"], offline_only=dead_zone,
                    allowlist=allowlist, agent_id=context.agent_id,
                    loaded_tools=context.extra["loaded_tools"],
                    defer_loading=defer_loading,
                )

            # ── max_tokens ──────────────────────────────────────────────────
            elif stop_reason == "max_tokens":
                log.warning(
                    "max_tokens_hit",
                    extra={"request_id": context.request_id, "iteration": iterations},
                )
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Please continue your response."}
                        ],
                    }
                )

            # ── pause_turn ──────────────────────────────────────────────────
            elif stop_reason == "pause_turn":
                log.info(
                    "pause_turn",
                    extra={"request_id": context.request_id},
                )
                messages.append(
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": "Please continue."}],
                    }
                )

            else:
                log.warning(
                    "unknown_stop_reason",
                    extra={
                        "request_id": context.request_id,
                        "stop_reason": stop_reason,
                    },
                )
                break

        # Emit a `file` event for each downloadable file produced this turn
        # (generate_document, save_file, sandbox deliver_file) so the UI renders a card.
        for meta in context.extra.get("produced_files", []):
            yield SSEEvent(
                type=SSEEventType.FILE,
                data=meta,
                session_id=context.session_id,
                request_id=context.request_id,
            )

        # Turn-level cost summary. The per-iteration `prompt_cache` lines say
        # what one call did; this says what the TURN cost and how much of it the
        # cache absorbed. `hit_rate` is the number to watch — a tool-using turn
        # that re-sends a 30k prefix ten times should be reading ~90% of its
        # input from cache. Anything near zero means the prefix is churning.
        _spend = context.extra.get("token_usage") or {}
        _read = _spend.get("cache_read", 0)
        log.info(
            "turn_cost",
            extra={
                "request_id": context.request_id,
                "model": context.model,
                "iterations": iterations,
                "input_read": _spend.get("input", 0),
                "billable_input": _spend.get("billable_input", 0),
                "cache_read": _read,
                "cache_write": _spend.get("cache_write", 0),
                "output": _spend.get("output", 0),
                "hit_rate": round(_read / max(1, _spend.get("input", 0)), 3),
            },
        )

        # DONE carries this turn's token spend so the UI can update its readout
        # immediately. It is a DELTA, not a total: persistence runs after the
        # generator finishes, so a client that refetched the session here would
        # still see the pre-turn total.
        yield SSEEvent(
            type=SSEEventType.DONE,
            data={"usage": context.extra.get("token_usage", {"input": 0, "output": 0})},
            session_id=context.session_id,
            request_id=context.request_id,
        )