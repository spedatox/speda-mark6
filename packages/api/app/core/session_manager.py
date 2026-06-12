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

    @staticmethod
    def stamp_user_content(content: list | str, created_at: datetime) -> list | str:
        """
        Prefix a user message with its timestamp, derived from the message's DB
        created_at — minute precision, always UTC.

        This is how SPEDA knows the current time: the newest user message's
        stamp IS "now". The clock used to live in the system prompt, where its
        minute-level churn changed the request prefix every turn and invalidated
        the conversation prompt-cache entry on EVERY provider (Anthropic
        explicit caching, OpenAI/Gemini implicit caching, Ollama's local KV
        cache are all byte-exact prefix matches). Stamps derived from stored
        created_at are byte-stable forever, so history reconstructed next turn
        is identical to what was cached this turn.
        """
        ts = created_at.strftime("[%Y-%m-%d %H:%M UTC]")
        if isinstance(content, str):
            return f"{ts} {content}" if content else ts
        return [{"type": "text", "text": ts}, *content]

    async def load_history(self, db: AsyncSession, session_id: int) -> list[dict]:
        """
        Load conversation history for a session in Anthropic messages format.
        Returns a list of {"role": ..., "content": ...} dicts. User messages are
        timestamp-stamped (see stamp_user_content) — deterministically from
        created_at, so the rendered history is byte-identical across turns.
        """
        result = await db.execute(
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(Message.created_at.asc())
        )
        messages = result.scalars().all()

        def _clean(content):
            # Strip SPEDA display-only blocks (tools/files metadata) before the
            # history goes back to Claude — they aren't valid Anthropic blocks.
            if not isinstance(content, list):
                return content
            cleaned = [
                b for b in content
                if not (isinstance(b, dict) and str(b.get("type", "")).startswith("_speda"))
            ]
            return cleaned or [{"type": "text", "text": ""}]

        out: list[dict] = []
        for m in messages:
            content = _clean(m.content)
            if m.role == "user":
                content = self.stamp_user_content(content, m.created_at)
            out.append({"role": m.role, "content": content})
        return out

    async def save_message(
        self,
        db: AsyncSession,
        session_id: int,
        role: str,
        content: list | str,
    ) -> Message:
        """Persist a message and return it (callers need created_at so the
        in-request stamp matches what load_history will reconstruct next turn)."""
        msg = Message(
            session_id=session_id,
            role=role,
            content=content if isinstance(content, list) else [{"type": "text", "text": content}],
        )
        db.add(msg)
        await db.commit()
        await db.refresh(msg)
        return msg
