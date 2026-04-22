import logging
from datetime import datetime

from fastapi import WebSocket
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import AgentRecord
from app.schemas.agent import AgentRegistration, AgentStatus
from app.websocket.manager import WebSocketManager
from app.websocket.protocol import AgentMessageType, TaskDispatch

logger = logging.getLogger(__name__)


class AgentRegistry:
    """
    Tracks online/offline presence of the Superior Six agents.
    In-memory dict updated by WebSocket lifecycle events.
    Persists all state changes to Postgres for audit.

    SPEDA checks this before delegating any task to an agent.
    If an agent is offline, SPEDA either handles the task itself or informs the user.
    """

    def __init__(self, ws_manager: WebSocketManager) -> None:
        self._ws_manager = ws_manager
        self._online: dict[str, AgentStatus] = {}

    async def register(
        self,
        websocket: WebSocket,
        registration: AgentRegistration,
        db: AsyncSession,
    ) -> None:
        await self._ws_manager.connect(registration.agent_id, websocket)

        status = AgentStatus(
            agent_id=registration.agent_id,
            agent_name=registration.agent_name,
            domain=registration.domain,
            status="online",
            last_seen=datetime.utcnow(),
            capabilities=registration.capabilities,
        )
        self._online[registration.agent_id] = status

        await self._persist(db, registration.agent_id, "online", registration.capabilities)
        logger.info("agent_online", extra={"agent_id": registration.agent_id})

    async def deregister(self, agent_id: str, db: AsyncSession) -> None:
        self._online.pop(agent_id, None)
        await self._ws_manager.disconnect(agent_id)
        await self._persist(db, agent_id, "offline")
        logger.info("agent_offline", extra={"agent_id": agent_id})

    def is_online(self, agent_id: str) -> bool:
        return agent_id in self._online

    def list_online(self) -> list[AgentStatus]:
        return list(self._online.values())

    async def dispatch(self, agent_id: str, task: TaskDispatch) -> None:
        """Push a task to a connected agent over WebSocket."""
        if not self.is_online(agent_id):
            logger.warning("agent_dispatch_offline", extra={"agent_id": agent_id})
            return
        await self._ws_manager.send(agent_id, task.model_dump())
        logger.info(
            "agent_task_dispatched",
            extra={"agent_id": agent_id, "task_id": task.task_id},
        )

    async def _persist(
        self,
        db: AsyncSession,
        agent_id: str,
        status: str,
        capabilities: list[str] | None = None,
    ) -> None:
        from sqlalchemy import select

        result = await db.execute(
            select(AgentRecord).where(AgentRecord.agent_id == agent_id)
        )
        record = result.scalar_one_or_none()

        if record:
            record.status = status
            record.last_seen = datetime.utcnow()
            if capabilities is not None:
                record.capabilities = {"tools": capabilities}
        else:
            record = AgentRecord(
                agent_id=agent_id,
                status=status,
                last_seen=datetime.utcnow(),
                capabilities={"tools": capabilities} if capabilities else None,
            )
            db.add(record)

        await db.commit()
