import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WebSocketManager:
    """
    Manages raw WebSocket connections for the Superior Six agents ONLY.
    Not used for Flutter user sessions.

    AgentRegistry calls into this for connection lifecycle.
    AgentOrchestrator never touches this directly.
    """

    def __init__(self) -> None:
        self._connections: dict[str, WebSocket] = {}

    async def connect(self, agent_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections[agent_id] = websocket
        logger.info("ws_connect", extra={"agent_id": agent_id})

    async def disconnect(self, agent_id: str) -> None:
        self._connections.pop(agent_id, None)
        logger.info("ws_disconnect", extra={"agent_id": agent_id})

    async def send(self, agent_id: str, message: dict[str, Any]) -> None:
        websocket = self._connections.get(agent_id)
        if websocket is None:
            logger.warning("ws_send_no_connection", extra={"agent_id": agent_id})
            return
        try:
            await websocket.send_json(message)
        except Exception as e:
            logger.error(
                "ws_send_error",
                extra={"agent_id": agent_id, "error": str(e)},
            )
            await self.disconnect(agent_id)

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Send a message to all connected agents. House Party Protocol stub."""
        for agent_id in list(self._connections.keys()):
            await self.send(agent_id, message)

    def is_connected(self, agent_id: str) -> bool:
        return agent_id in self._connections

    def connected_agents(self) -> list[str]:
        return list(self._connections.keys())
