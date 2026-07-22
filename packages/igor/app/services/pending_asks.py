"""Pending permission asks — the relay between a peer's gate and the owner.

The Forge's safety gate stops irreversible operations (force-push, recursive
delete, writes into `.git`) and asks rather than dead-ending the job. This holds
the question while the owner decides, and sends the answer back down the peer
socket.

**Igor decides nothing here.** It stores, forwards, and correlates. The peer's
gate classified the action, the owner makes the call, and this is the wire in
between — which is why it is a service holding a dict and not policy.

**Absence is never approval.** An ask that is never answered expires, and an
expired ask is a denial: the peer's own timeout fires at roughly the same moment
and denies locally, so nothing here can grant permission by forgetting. The two
timeouts are independent on purpose — if this process dies, the peer still
refuses on its own.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# A little longer than the peer's own ask timeout (FORGE_ASK_TIMEOUT_S, 120s), so
# the peer is always the first to give up. If Igor expired first it would drop
# the record while the peer was still waiting, and a late approval would arrive
# with nowhere to go.
DEFAULT_TTL_S = 150.0


@dataclass
class PendingAsk:
    ask_id: str
    agent_id: str
    tool: str
    action_key: str
    reason: str
    job_id: str = ""
    chat_id: str | None = None
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at

    @property
    def seconds_left(self) -> float:
        return max(0.0, self.expires_at - time.time())

    def to_dict(self) -> dict:
        return {
            "ask_id": self.ask_id,
            "agent_id": self.agent_id,
            "tool": self.tool,
            "action_key": self.action_key,
            "reason": self.reason,
            "job_id": self.job_id,
            "chat_id": self.chat_id,
            "seconds_left": round(self.seconds_left, 1),
        }


class PendingAsks:
    """Open questions from external peers, keyed by ask_id."""

    def __init__(self, ws_manager, ttl_s: float = DEFAULT_TTL_S) -> None:
        self._ws = ws_manager
        self._ttl = ttl_s
        self._asks: dict[str, PendingAsk] = {}
        self._lock = asyncio.Lock()

    # ── Ingress ──────────────────────────────────────────────────────────────
    def record(self, agent_id: str, frame: dict) -> PendingAsk | None:
        """Store one `permission_request`. None if the frame is unusable.

        A malformed frame is dropped rather than stored: an ask with no id can
        never be answered, so keeping it would only leak a row into the tray
        that no click could clear."""
        ask_id = str(frame.get("ask_id") or "").strip()
        tool = str(frame.get("tool") or "").strip()
        if not ask_id or not tool:
            logger.warning("permission_request_malformed",
                           extra={"agent_id": agent_id, "ask_id": ask_id})
            return None

        # Honour the peer's own timeout when it sends one, so the countdown the
        # owner sees matches the one actually running on the other end.
        ttl = self._ttl
        peer_timeout = frame.get("timeout_s")
        if peer_timeout is not None:
            try:
                ttl = max(5.0, float(peer_timeout) + 30.0)
            except (TypeError, ValueError):
                ttl = self._ttl

        ask = PendingAsk(
            ask_id=ask_id,
            agent_id=agent_id,
            tool=tool,
            action_key=str(frame.get("action_key") or ""),
            reason=str(frame.get("reason") or ""),
            job_id=str(frame.get("job_id") or ""),
            chat_id=(str(frame["chat_id"]) if frame.get("chat_id") else None),
        )
        ask.expires_at = ask.created_at + ttl
        self._asks[ask_id] = ask
        logger.info("permission_request_received",
                    extra={"agent_id": agent_id, "ask_id": ask_id, "tool": tool})
        return ask

    # ── Egress ───────────────────────────────────────────────────────────────
    async def answer(self, ask_id: str, approved: bool, remember: bool = False,
                     note: str = "") -> bool:
        """Send the owner's decision to the peer. False if nothing was waiting.

        Held under a lock so a double-click cannot send two answers for one
        question: the peer resolves the first and logs the second as unmatched,
        which is harmless but makes the logs lie about what the owner did."""
        async with self._lock:
            ask = self._asks.pop(ask_id, None)
            if ask is None:
                logger.info("permission_answer_unmatched", extra={"ask_id": ask_id})
                return False

            await self._ws.send(ask.agent_id, {
                "type": "permission_response",
                "ask_id": ask_id,
                "approved": bool(approved),
                "remember": bool(remember),
                "note": note or "",
            })
            logger.info("permission_answered",
                        extra={"ask_id": ask_id, "agent_id": ask.agent_id,
                               "approved": bool(approved), "remember": bool(remember)})
            return True

    # ── Housekeeping ─────────────────────────────────────────────────────────
    def list_open(self) -> list[PendingAsk]:
        """Live asks, oldest first — the tray reads this."""
        self.sweep()
        return sorted(self._asks.values(), key=lambda a: a.created_at)

    def sweep(self) -> int:
        """Drop expired asks. Returns how many went.

        Nothing is sent to the peer: it ran its own timeout and already denied
        locally. Sending a late denial would be answering a question that is no
        longer being asked."""
        gone = [ask_id for ask_id, ask in self._asks.items() if ask.expired]
        for ask_id in gone:
            self._asks.pop(ask_id, None)
        if gone:
            logger.info("permission_asks_expired", extra={"count": len(gone)})
        return len(gone)

    def drop_agent(self, agent_id: str) -> int:
        """Forget every ask from a peer that disconnected. Its questions died
        with the socket, and the peer denies them on its side during teardown."""
        gone = [ask_id for ask_id, ask in self._asks.items() if ask.agent_id == agent_id]
        for ask_id in gone:
            self._asks.pop(ask_id, None)
        if gone:
            logger.info("permission_asks_dropped_agent",
                        extra={"agent_id": agent_id, "count": len(gone)})
        return len(gone)
