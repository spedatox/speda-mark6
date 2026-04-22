import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.session import Session
from app.models.message import Message

logger = logging.getLogger(__name__)


class SessionManager:
    """
    Manages conversation session lifecycle and history loading.
    Lives at Phase 9.5 — AgentContext construction depends on it.
    Injected into app.state in the lifespan handler.
    """

    async def get_or_create(
        self,
        db: AsyncSession,
        user_id: int,
        triggered_by: str,
        model_used: str,
        agent_id: str = "speda",
        session_id: int | None = None,
    ) -> Session:
        """
        Return an existing session by ID, or create a new one.
        Always creates a new session if session_id is None.
        """
        if session_id is not None:
            result = await db.execute(select(Session).where(Session.id == session_id))
            existing = result.scalar_one_or_none()
            if existing:
                return existing

        session = Session(
            user_id=user_id,
            agent_id=agent_id,
            triggered_by=triggered_by,
            model_used=model_used,
            started_at=datetime.utcnow(),
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)
        logger.info(
            "session_created",
            extra={"session_id": session.id, "triggered_by": triggered_by},
        )
        return session

    async def close(self, db: AsyncSession, session_id: int) -> None:
        """Mark a session as ended."""
        result = await db.execute(select(Session).where(Session.id == session_id))
        session = result.scalar_one_or_none()
        if session:
            session.ended_at = datetime.utcnow()
            await db.commit()
            logger.info("session_closed", extra={"session_id": session_id})

    async def load_history(self, db: AsyncSession, session_id: int) -> list[dict]:
        """
        Load conversation history for a session in Anthropic messages format.
        Returns a list of {"role": ..., "content": ...} dicts.
        """
        result = await db.execute(
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(Message.created_at.asc())
        )
        messages = result.scalars().all()
        return [{"role": m.role, "content": m.content} for m in messages]

    async def save_message(
        self,
        db: AsyncSession,
        session_id: int,
        role: str,
        content: list | str,
    ) -> None:
        """Persist a message to the database."""
        msg = Message(
            session_id=session_id,
            role=role,
            content=content if isinstance(content, list) else [{"type": "text", "text": content}],
        )
        db.add(msg)
        await db.commit()
