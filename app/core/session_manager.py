import logging
import json
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
            result = await db.execute(select(Session).where(Session.id == session_id, Session.user_id == user_id))
            existing = result.scalar_one_or_none()
            if existing:
                from app.models.coordination import Room
                if await db.scalar(select(Room.id).where(Room.session_id == existing.id)):
                    raise ValueError("This session belongs to a room; use its coordination endpoint")
                if existing.agent_id != agent_id:
                    raise ValueError("Session belongs to a different agent")
                return existing
            raise ValueError("Session is not available to this owner")

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
            .order_by(Message.id.desc()).limit(64)
        )
        messages = list(reversed(result.scalars().all()))
        while messages and (messages[0].role != "user" or
              (isinstance(messages[0].content, list) and any(
                  b.get("type") == "tool_result" for b in messages[0].content))):
            messages.pop(0)
        # Evict complete older turns under a text/transport budget, never mark
        # their originals summarized. The memory source index retains them.
        while messages and sum(len(json.dumps(m.content).encode("utf-8")) for m in messages) > 64000:
            next_turn = next((i for i, message in enumerate(messages[1:], 1)
                if message.role == "user" and not any(b.get("type") == "tool_result"
                    for b in message.content if isinstance(b, dict))), len(messages))
            messages = messages[next_turn:]
        return [{"role": m.role, "content": m.content} for m in messages]

    async def save_message(
        self,
        db: AsyncSession,
        session_id: int,
        role: str,
        content: list | str,
    ) -> Message:
        """Persist a message to the database."""
        msg = Message(
            session_id=session_id,
            role=role,
            content=content if isinstance(content, list) else [{"type": "text", "text": content}],
        )
        db.add(msg)
        await db.flush()
        if role == "user":
            session = (await db.execute(select(Session).where(Session.id == session_id))).scalar_one()
            if session.triggered_by == "user":
                from app.services.memory_store import capture_source, source_text
                previous = (await db.execute(select(Message).where(
                    Message.session_id == session_id, Message.id < msg.id
                ).order_by(Message.id.desc()).limit(1))).scalar_one_or_none()
                prior_text = source_text(previous.content) if previous else ""
                prior_context = ({"message_id": previous.id, "role": previous.role,
                    "text": prior_text[-1200:], "fragment": len(prior_text) > 1200,
                    "authority": "context_only_not_owner_evidence"} if previous else None)
                await capture_source(db, user_id=session.user_id, source_key=f"message:{msg.id}",
                    content=source_text(msg.content), session_id=session_id, message_id=msg.id,
                    provenance={"prior_context": prior_context})
        await db.commit()
        return msg
