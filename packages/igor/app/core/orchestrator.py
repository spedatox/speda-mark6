import asyncio
import logging
from typing import AsyncGenerator

from app.core.context import AgentContext
from app.core.registry import CapabilityRegistry
from app.profiles.registry import ProfileRegistry
from app.schemas.sse import SSEEvent, SSEEventType
from app.services.llm_client import LLMClient, blocks_to_dicts
from app.skills.memory import MemoryRecallCache, recall_for_context, recall_sessions_for_context

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 30  # Safety guard — Rule 4a


class AgentOrchestrator:
    """
    Owns the agentic loop and the system prompt.
    Neither lives anywhere else (CLAUDE.md Rules 1, 2, 4).

    Stateless with respect to identity: it holds the ProfileRegistry, not one
    profile, and resolves the agent per request from context.agent_id. The same
    loop serves every in-process agent (SPEDA + the five Superior Six).

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
        # full registry, e.g. SPEDA the orchestrator) governs what this agent can
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
        # SPEDA becomes mission commander; every other agent becomes an operative
        # that accepts tasks outside its domain. Engaged/stood down via the
        # house_party tool on the owner's explicit invocation.
        from app.core.runtime_state import get_house_party
        from app.profiles.registry import DEFAULT_AGENT_ID
        if get_house_party():
            if context.agent_id == DEFAULT_AGENT_ID:
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
                    "the owner: what was done, by whom, what it means, what's next.\n"
                    "Keep the owner informed of who is working on what. Stand the "
                    "protocol down (house_party tool) when the owner says the "
                    "situation is resolved."
                )
            else:
                stable_core += (
                    "\n\n## HOUSE PARTY PROTOCOL — ACTIVE\n\n"
                    "The owner has engaged the all-hands protocol. You are an "
                    "OPERATIVE on a high-stakes mission: tasks dispatched to you may "
                    "fall outside your usual domain — take them anyway and deliver "
                    "your best work; specialization is a preference here, not a "
                    "boundary. Check the network channel in your briefing so you "
                    "build on the other agents' results instead of duplicating them. "
                    "Report substance, fast."
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
            "local timezone, formatted [YYYY-MM-DD HH:MM TZ] (TZ is the zone "
            "abbreviation or UTC offset). The newest user message's stamp IS the "
            "current date and time — trust it over any internal sense of time, "
            "and speak in this local time unless asked otherwise.\n"
            f"Owner timezone: {context.timezone}\n"
            f"Active model: {context.model}"
        )

        # ── Automated-run discipline ────────────────────────────────────────
        # A trigger fires with no human in the loop. Weaker non-Anthropic models
        # (SPEDA runs on open models in prod) will happily write a plausible
        # briefing WITHOUT calling any tool if left to their own devices — the
        # daily-brief "pure hallucination" bug. This standing directive is
        # model-agnostic (the ollama block below is greeting-discipline, not
        # this) and forces execute-over-narrate + a hard no-fabrication rule.
        if context.triggered_by != "user":
            stable_core += (
                "\n\n## AUTOMATED RUN — EXECUTE, DON'T NARRATE\n\n"
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

        # Catalog of lazily-loadable toolsets (small, stable → cached). SPEDA
        # pulls a toolset in via use_toolset only when a task needs it, keeping
        # the prompt prefix tiny instead of shipping every MCP tool every call.
        # Pointless in a dead zone — every loadable toolset is remote.
        catalog = "" if dead_zone else self._registry.toolset_catalog(allowlist=allowlist)
        if catalog:
            stable_core = f"{stable_core}\n\n{catalog}"

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

        # Keep a plain-string copy for any downstream logging/inspection.
        context.system_prompt = stable_core

        # Toolsets loaded this turn (grows when use_toolset is called).
        context.extra.setdefault("active_servers", set())

        messages = list(context.conversation_history)
        tools = self._registry.list_tools(
            context.extra["active_servers"], offline_only=dead_zone,
            allowlist=allowlist, agent_id=context.agent_id,
        )
        iterations = 0
        produced_text = False  # any text streamed yet this turn (for paragraph breaks)

        yield SSEEvent(
            type=SSEEventType.START,
            data={"tools_available": len(tools)},
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
                log.info(
                    "prompt_cache",
                    extra={
                        "request_id": context.request_id,
                        "cache_read": getattr(usage, "cache_read_input_tokens", 0) or 0,
                        "cache_write": getattr(usage, "cache_creation_input_tokens", 0) or 0,
                        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
                    },
                )

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

                # 2. Execute all tools in parallel
                exec_tasks = [
                    self._registry.execute(block.name, block.input, context)
                    for block in tool_use_blocks
                ]
                results = await asyncio.gather(*exec_tasks)

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

                # A use_toolset call may have loaded new toolsets — rebuild the
                # tool list so they're available on the next iteration.
                tools = self._registry.list_tools(
                    context.extra["active_servers"], offline_only=dead_zone,
                    allowlist=allowlist, agent_id=context.agent_id,
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

        yield SSEEvent(
            type=SSEEventType.DONE,
            data={},
            session_id=context.session_id,
            request_id=context.request_id,
        )