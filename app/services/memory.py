import logging

from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import AgentContext
from app.core.session_manager import SessionManager

logger = logging.getLogger(__name__)


async def extract_memory(context: AgentContext, full_response: str) -> None:
    """
    Background task: extract facts from the completed conversation and persist.
    Runs as a FastAPI BackgroundTask — never inside the SSE generator (CLAUDE.md Rule 7).
    TODO: Implement fact extraction via Haiku (low-effort, background).
    """
    logger.info(
        "memory_extract_start",
        extra={"request_id": context.request_id, "session_id": context.session_id},
    )
    # TODO: Call Haiku with extraction prompt, persist to a memory/facts table.


async def generate_title(context: AgentContext, session_manager: SessionManager) -> None:
    """
    Background task: generate a short conversation title after the first exchange.
    Runs as a FastAPI BackgroundTask — never inside the SSE generator (CLAUDE.md Rule 7).
    TODO: Call Haiku with a title generation prompt, update sessions.title.
    """
    logger.info(
        "title_generate_start",
        extra={"request_id": context.request_id, "session_id": context.session_id},
    )
    # TODO: Generate title via Haiku and persist to sessions table.


def schedule_background_tasks(
    background_tasks: BackgroundTasks,
    context: AgentContext,
    full_response: str,
    session_manager: SessionManager,
) -> None:
    """
    Schedule memory extraction and title generation as background tasks.
    Called from the router after the SSE stream completes.
    """
    background_tasks.add_task(extract_memory, context, full_response)
    background_tasks.add_task(generate_title, context, session_manager)
