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
        """
        Non-streaming message creation. Used by Task sub-agents and small
        background tasks.

        Caching IS applied here too: sub-agents carry the full tool prefix
        (~38k tokens with all MCP servers) and run multiple iterations — without
        caching, each iteration re-sends the entire prefix uncached, which both
        explodes cost and blows the per-minute input-token rate limit (429s).
        Background Haiku tasks pass a tiny system and no tools, so caching is a
        no-op there (the API ignores sub-minimum blocks).
        """
        return await self._client.messages.create(**_apply_prompt_caching(kwargs))

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

    The Anthropic request prefix is [tools, system, messages]. Caching keys on a
    byte-identical prefix, so ONLY content that doesn't change between calls may
    sit inside a cached block. We place breakpoints on:

      - the last tool definition (all tools are identical every call), and
      - every system block the caller flagged with `_cache: True`
        (the orchestrator flags the stable core prompt and the memory block, and
        leaves the volatile datetime/model tail unflagged → uncached).

    TTL: we use a 1-hour cache. The cache is content-keyed server-side at
    Anthropic, so a 1h window means the ~15k-token prefix is written roughly once
    per hour instead of once per 5 minutes — and crucially it SURVIVES backend
    restarts (a restart re-sends identical content → cache hit, no rewrite). The
    1h write costs 1.6x a 5m write but is amortised over ~12x fewer writes.

    Caching is ignored automatically by the API when a block is under the minimum
    cacheable size, so this is always safe to apply.
    """
    out = dict(kwargs)
    cache_control = {"type": "ephemeral", "ttl": settings.prompt_cache_ttl}

    # System: list of blocks. Cache each block flagged with `_cache`, strip the
    # marker (the API rejects unknown keys). A bare string is treated as one
    # cached block for backward compatibility.
    system = out.get("system")
    if isinstance(system, str) and system:
        out["system"] = [
            {"type": "text", "text": system, "cache_control": cache_control}
        ]
    elif isinstance(system, list):
        new_system = []
        for blk in system:
            b = dict(blk)
            should_cache = b.pop("_cache", False)
            if should_cache:
                b["cache_control"] = cache_control
            new_system.append(b)
        out["system"] = new_system

    # Tools: mark the last tool so the whole tool block is cached.
    tools = out.get("tools")
    if tools:
        cached_tools = [dict(t) for t in tools]
        cached_tools[-1] = {**cached_tools[-1], "cache_control": cache_control}
        out["tools"] = cached_tools

    # Conversation history: incremental caching. Mark the LAST message's last
    # content block — messages are append-only within a session, so on the next
    # turn the whole prior history sits in the cached prefix and is read cheaply
    # instead of re-sent at full input price. Without this, a long multi-turn
    # chat re-sends the entire growing transcript uncached on every turn.
    # Breakpoint budget stays within Anthropic's max of 4:
    #   tools(1) + stable system(1) + memory system(≤1) + conversation(1).
    messages = out.get("messages")
    if messages:
        msgs = [dict(m) for m in messages]
        last = dict(msgs[-1])
        content = last.get("content")
        if isinstance(content, str) and content:
            last["content"] = [
                {"type": "text", "text": content, "cache_control": cache_control}
            ]
        elif isinstance(content, list) and content:
            new_content = [dict(b) for b in content]
            new_content[-1] = {**new_content[-1], "cache_control": cache_control}
            last["content"] = new_content
        msgs[-1] = last
        out["messages"] = msgs

    return out
