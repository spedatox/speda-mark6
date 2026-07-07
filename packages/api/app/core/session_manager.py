import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.session import Session
from app.models.message import Message

logger = logging.getLogger(__name__)


class SessionManager:
    # Per-session loaded-toolset memory: once a server is loaded via use_toolset
    # in a session, it stays in the tool list for every subsequent turn — so the
    # tool array (and therefore the cached prefix) is stable. Without this,
    # active_servers resets to empty on each HTTP request, the model re-calls
    # use_toolset, the tool list changes, and the entire prompt cache is rewritten
    # at 2x cost. This dict is process-local (not persisted) — a server restart
    # clears it, which is fine (one cache write to re-establish).
    _session_servers: dict[int, set[str]] = {}

    # Sticky channel sessions: a non-"app" channel (Telegram) has no session_id
    # to pass per turn, so we pin one open session per (user, agent, channel) and
    # reuse it until /new. Process-local; a restart re-pins to the newest open DB
    # session for that tuple (see get_or_create). Keyed by (user_id, agent_id,
    # channel) → session_id.
    _channel_sessions: dict[tuple[int, str, str], int] = {}

    def get_loaded_servers(self, session_id: int) -> set[str]:
        return set(self._session_servers.get(session_id, set()))

    def mark_servers_loaded(self, session_id: int, servers: set[str]) -> None:
        existing = self._session_servers.get(session_id, set())
        self._session_servers[session_id] = existing | servers

    async def reset_channel_session(
        self, db: AsyncSession, channel: str, agent_id: str, user_id: int = 1
    ) -> None:
        """Close the sticky session for a channel and drop its pin so the next
        turn starts a fresh one (the /new command). Marks every still-open session
        for the tuple ended so the DB-adoption path in get_or_create doesn't just
        re-adopt it; the old messages are untouched (the transcript survives)."""
        self._channel_sessions.pop((user_id, agent_id, channel), None)
        result = await db.execute(
            select(Session).where(
                Session.user_id == user_id,
                Session.agent_id == agent_id,
                Session.channel == channel,
                Session.ended_at.is_(None),
            )
        )
        now = datetime.utcnow()
        closed = 0
        for sess in result.scalars().all():
            sess.ended_at = now
            closed += 1
        if closed:
            await db.commit()
        logger.info(
            "channel_session_reset",
            extra={"agent_id": agent_id, "channel": channel, "closed": closed},
        )
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
        channel: str = "app",
    ) -> Session:
        """
        Return an existing session by ID, the pinned sticky session for a
        non-"app" channel, or create a new one.

        - session_id given → that session (app chat passes it every turn).
        - channel != "app" and no session_id → the STICKY session for
          (user, agent, channel): the in-process pin, or the newest open session
          in the DB for that tuple (re-pins across restarts), or a fresh one.
        - otherwise → always a new session (app default, unchanged).
        """
        if session_id is not None:
            result = await db.execute(select(Session).where(Session.id == session_id))
            existing = result.scalar_one_or_none()
            if existing:
                return existing

        if channel != "app":
            key = (user_id, agent_id, channel)
            pinned = self._channel_sessions.get(key)
            if pinned is not None:
                result = await db.execute(select(Session).where(Session.id == pinned))
                existing = result.scalar_one_or_none()
                if existing and existing.ended_at is None:
                    return existing
            # No live pin — adopt the newest open session for this tuple if one
            # exists (survives a restart), else fall through to create.
            result = await db.execute(
                select(Session)
                .where(
                    Session.user_id == user_id,
                    Session.agent_id == agent_id,
                    Session.channel == channel,
                    Session.ended_at.is_(None),
                )
                .order_by(Session.started_at.desc())
                .limit(1)
            )
            adopted = result.scalar_one_or_none()
            if adopted is not None:
                self._channel_sessions[key] = adopted.id
                return adopted

        session = Session(
            user_id=user_id,
            agent_id=agent_id,
            channel=channel,
            triggered_by=triggered_by,
            model_used=model_used,
            started_at=datetime.utcnow(),
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)
        if channel != "app":
            self._channel_sessions[(user_id, agent_id, channel)] = session.id
        logger.info(
            "session_created",
            extra={"session_id": session.id, "triggered_by": triggered_by, "channel": channel},
        )
        return session

    async def list_sessions(
        self,
        db: AsyncSession,
        user_id: int,
        agent_id: str,
        limit: int = 500,
    ) -> list[Session]:
        """Sessions for one (user, agent), newest first. Scoped by agent_id so
        one agent's history never leaks into another's list (CLAUDE.md
        SessionManager contract). Backed by ix_sessions_user_agent_started."""
        result = await db.execute(
            select(Session)
            .where(Session.user_id == user_id, Session.agent_id == agent_id)
            .order_by(Session.started_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

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

        If the session has been compacted, only messages AFTER the summary
        watermark are loaded, with the rolling summary prepended — so a long
        chat sends [summary] + [recent window] instead of the full transcript.
        """
        sess = (
            await db.execute(select(Session).where(Session.id == session_id))
        ).scalar_one_or_none()
        summary = sess.summary if sess else None
        through_id = (sess.summary_through_id if sess else None) or 0

        stmt = select(Message).where(Message.session_id == session_id)
        if summary and through_id:
            stmt = stmt.where(Message.id > through_id)
        messages = (
            await db.execute(stmt.order_by(Message.created_at.asc()))
        ).scalars().all()

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

        if summary:
            out = self._prepend_summary(summary, out)
        return out

    @staticmethod
    def _prepend_summary(summary: str, messages: list[dict]) -> list[dict]:
        """Inject the compaction summary at the front of the history. Merged into
        the first message if it's a user turn (so we never emit two user turns in
        a row); otherwise inserted as a standalone leading user message."""
        block = {
            "type": "text",
            "text": (
                "[EARLIER CONVERSATION — COMPACTED]\n"
                "The opening of this conversation was summarized to save context. "
                "Treat the following as established background you already know "
                "and continue seamlessly:\n\n"
                f"{summary}\n\n"
                "[END SUMMARY — the most recent messages follow verbatim]"
            ),
        }
        if messages and messages[0]["role"] == "user":
            first = dict(messages[0])
            content = first["content"]
            if isinstance(content, list):
                first["content"] = [block, *content]
            else:
                first["content"] = [block, {"type": "text", "text": str(content)}]
            return [first, *messages[1:]]
        return [{"role": "user", "content": [block]}, *messages]

    async def truncate(self, db: AsyncSession, session_id: int, keep: int) -> int:
        """
        Keep the first `keep` messages of a session (oldest first) and delete the
        rest. Powers regenerate (drop the last assistant turn) and edit (drop the
        old user turn + everything after it) so the model genuinely re-runs from
        a clean history instead of seeing its previous answer.

        Position-based to match the client, which only knows message ORDER, not
        DB ids (freshly streamed messages never carried a DB id). Returns the
        number of rows deleted.
        """
        keep = max(0, keep)
        result = await db.execute(
            select(Message.id)
            .where(Message.session_id == session_id)
            .order_by(Message.created_at.asc())
            .offset(keep)
        )
        ids = [row[0] for row in result.all()]
        if not ids:
            return 0
        from sqlalchemy import delete as _delete

        await db.execute(_delete(Message).where(Message.id.in_(ids)))
        await db.commit()
        logger.info(
            "session_truncated",
            extra={"session_id": session_id, "kept": keep, "deleted": len(ids)},
        )
        return len(ids)

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
