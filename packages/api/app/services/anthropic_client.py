import logging
from typing import AsyncIterator

import anthropic

from app.config import settings

logger = logging.getLogger(__name__)


class AnthropicClient:
    """
    Thin wrapper around the Anthropic async SDK.
    Injected into AgentOrchestrator at startup via app.state.
    All API calls go through here — never instantiate anthropic.AsyncAnthropic elsewhere.
    """

    def __init__(self) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    @property
    def client(self) -> anthropic.AsyncAnthropic:
        return self._client

    async def create_message(self, **kwargs) -> anthropic.types.Message:
        """Non-streaming message creation. Used by small background tasks (no caching)."""
        return await self._client.messages.create(**kwargs)

    def stream_message(self, **kwargs) -> anthropic.AsyncMessageStream:
        """
        Returns a streaming context manager for the main agentic loop. Usage:
            async with client.stream_message(...) as stream:
                async for text in stream.text_stream:
                    yield text
                final = await stream.get_final_message()

        Prompt caching is applied automatically to the large, stable prefix
        (tool definitions + system prompt) so it isn't re-billed at full price
        on every iteration / turn within the cache window.
        """
        return self._client.messages.stream(**_apply_prompt_caching(kwargs))


def _apply_prompt_caching(kwargs: dict) -> dict:
    """
    Insert ephemeral cache breakpoints on the stable request prefix.

    The Anthropic request prefix is [tools, system, messages]. We place one
    breakpoint on the last tool (the 35 tool definitions are identical on every
    call → near-100% cache hit) and one on the system prompt (identical across
    the agentic loop's iterations and within a session → hits on iterations 1+
    and on subsequent turns inside the 5-minute window). Only the variable
    messages are billed at full input price.

    Caching is ignored automatically by the API when the prefix is under the
    minimum cacheable size, so this is always safe to apply.
    """
    out = dict(kwargs)

    # System prompt: string → a single cached text block.
    system = out.get("system")
    if isinstance(system, str) and system:
        out["system"] = [
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
        ]

    # Tools: mark the last tool so the whole tool block is cached.
    tools = out.get("tools")
    if tools:
        cached_tools = [dict(t) for t in tools]
        cached_tools[-1] = {**cached_tools[-1], "cache_control": {"type": "ephemeral"}}
        out["tools"] = cached_tools

    return out
