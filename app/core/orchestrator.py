import logging
from datetime import datetime, timezone
from typing import AsyncGenerator

from app.core.context import AgentContext
from app.core.registry import CapabilityRegistry
from app.profiles.base import AgentProfile
from app.schemas.sse import SSEEvent, SSEEventType
from app.services.anthropic_client import AnthropicClient

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

        # Build system prompt and inject into context
        context.system_prompt = self.build_system_prompt(context)

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

            # ── Call Claude ─────────────────────────────────────────────────
            log.info(
                "claude_call",
                extra={
                    "request_id": context.request_id,
                    "model": context.model,
                    "messages": len(messages),
                    "iteration": iterations,
                },
            )

            response = await self._client.create_message(
                model=context.model,
                system=context.system_prompt,
                messages=messages,
                tools=tools,
                max_tokens=8096,
            )

            stop_reason = response.stop_reason

            # Convert content blocks to serialisable dicts for message history
            assistant_content = _blocks_to_dicts(response.content)
            messages.append({"role": "assistant", "content": assistant_content})

            # ── end_turn ────────────────────────────────────────────────────
            if stop_reason == "end_turn":
                for block in response.content:
                    if hasattr(block, "text") and block.text:
                        yield SSEEvent(
                            type=SSEEventType.CHUNK,
                            data=block.text,
                            session_id=context.session_id,
                            request_id=context.request_id,
                        )
                break

            # ── tool_use ────────────────────────────────────────────────────
            elif stop_reason == "tool_use":
                iterations += 1
                tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

                # Yield any text Claude produced before the tool calls
                for block in response.content:
                    if hasattr(block, "text") and block.text:
                        yield SSEEvent(
                            type=SSEEventType.CHUNK,
                            data=block.text,
                            session_id=context.session_id,
                            request_id=context.request_id,
                        )

                tool_results = []
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
                    result = await self._registry.execute(
                        tool_block.name, tool_block.input, context
                    )
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_block.id,
                            "content": result,
                        }
                    )

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
