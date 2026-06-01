import asyncio
import logging
from datetime import datetime, timezone
from typing import AsyncGenerator

from app.core.context import AgentContext
from app.core.registry import CapabilityRegistry
from app.profiles.base import AgentProfile
from app.schemas.sse import SSEEvent, SSEEventType
from app.services.anthropic_client import AnthropicClient
from app.skills.memory import recall_for_context

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 30  # Safety guard — Rule 4a


class AgentOrchestrator:
    """
    Owns the agentic loop and the system prompt.
    Neither lives anywhere else (CLAUDE.md Rules 1, 2, 4).

    Router contract: call run(context) and stream the SSEEvents.
    Zero logic in the router beyond that.
    """

    def __init__(
        self,
        registry: CapabilityRegistry,
        client: AnthropicClient,
        profile: AgentProfile,
    ) -> None:
        self._registry = registry
        self._client = client
        self._profile = profile

    def build_system_prompt(self, context: AgentContext) -> str:
        """
        Build the full system prompt from the profile template + runtime context vars.
        Only called here — never in a router, never in a service.
        """
        return self._profile.build_system_prompt(
            {
                "current_datetime": datetime.now(timezone.utc).strftime(
                    "%A, %d %B %Y %H:%M UTC"
                ),
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

        # Build the system prompt as THREE blocks, ordered for prompt caching.
        # Caching keys on byte-identical prefixes, so volatile content must come
        # last and stay OUT of the cached blocks (see anthropic_client._apply_prompt_caching):
        #
        #   1. stable_core   — identity + policies + tool guidance. Never changes
        #                      between turns → cached (biggest block, ~13k tokens).
        #   2. memory_block  — owner/current/dossier/history + size-free listing.
        #                      Changes at most ~daily → cached.
        #   3. volatile_tail — current datetime + active model. Changes every
        #                      minute → NEVER cached (tiny, ~30 tokens).
        #
        # Previously all three were concatenated into one cached block, so the
        # minute-precision clock busted the entire ~15k-token cache every minute.
        stable_core = self.build_system_prompt(context)

        # Budget mode — hard frugality directive baked into the cached prompt.
        from app.config import settings
        if settings.budget_mode:
            stable_core += (
                "\n\n## BUDGET MODE — ACTIVE\n\n"
                "The owner is on a strict budget. Enforce this every turn:\n"
                "- Keep answers SHORT — the minimum that fully answers the question. "
                "A few sentences or bullets. No multi-section reports, no scenario "
                "tables, unless the owner explicitly says 'deep dive' / 'full briefing'.\n"
                "- Run as FEW web searches as possible (ideally 1, at most 2-3).\n"
                "- Sub-agents are disabled. Do all work yourself in this turn.\n"
                "- If a request truly warrants depth, give a short answer first and "
                "ask whether to expand — never assume."
            )

        memory_block = ""
        if context.db is not None:
            try:
                memory_block = await recall_for_context(context.user_id, context.db) or ""
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

        now = datetime.now(timezone.utc).strftime("%A, %d %B %Y %H:%M UTC")
        volatile_tail = (
            f"## Now\n\nCurrent date and time: {now}\nActive model: {context.model}"
        )

        # Structured system blocks. `_cache: True` marks the block for an ephemeral
        # cache breakpoint; the marker is stripped before the request is sent.
        system_blocks: list[dict] = [{"type": "text", "text": stable_core, "_cache": True}]
        if memory_block:
            system_blocks.append({"type": "text", "text": memory_block, "_cache": True})
        system_blocks.append({"type": "text", "text": volatile_tail})  # uncached

        # Keep a plain-string copy for any downstream logging/inspection.
        context.system_prompt = stable_core

        messages = list(context.conversation_history)
        tools = self._registry.list_tools()
        iterations = 0

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
                async for delta in stream.text_stream:
                    if delta:
                        yield SSEEvent(
                            type=SSEEventType.CHUNK,
                            data=delta,
                            session_id=context.session_id,
                            request_id=context.request_id,
                        )
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
            assistant_content = _blocks_to_dicts(response.content)
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

                # 1. Stream all the TOOL start events to the frontend immediately
                for tool_block in tool_use_blocks:
                    yield SSEEvent(
                        type=SSEEventType.TOOL,
                        data={"name": tool_block.name, "id": tool_block.id},
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

        yield SSEEvent(
            type=SSEEventType.DONE,
            data={},
            session_id=context.session_id,
            request_id=context.request_id,
        )


def _blocks_to_dicts(content_blocks) -> list[dict]:
    """Convert Anthropic SDK ContentBlock objects to plain dicts for message history."""
    result = []
    for block in content_blocks:
        if block.type == "text":
            result.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            result.append(
                {
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                }
            )
        else:
            # Pass through unknown block types as-is
            try:
                result.append(block.model_dump())
            except Exception:
                result.append({"type": block.type})
    return result