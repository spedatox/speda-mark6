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
        """Non-streaming message creation. Used inside the agentic loop."""
        return await self._client.messages.create(**kwargs)

    def stream_message(self, **kwargs) -> anthropic.AsyncMessageStream:
        """
        Returns a streaming context manager. Usage:
            async with client.stream_message(...) as stream:
                async for text in stream.text_stream:
                    yield text
                final = await stream.get_final_message()
        """
        return self._client.messages.stream(**kwargs)
