# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

import logging
from typing import Any

from fastapi import WebSocket

from app.core.peer_routing import LINUX, PeerInfo, resolve

logger = logging.getLogger(__name__)

DEFAULT_HOST = "default"


class WebSocketManager:
    """
    Manages raw WebSocket connections for external peer agents (Optimus).
    Not used for Flutter user sessions, and not used for in-process profiles.

    AgentRegistry calls into this for connection lifecycle.
    AgentOrchestrator never touches this directly.

    One agent may attach from SEVERAL machines at once — Optimus running on the
    server and on the owner's PC is still ONE agent (same profile, same memory,
    same sessions; the peer is stateless per turn). So connections are keyed by
    (agent_id, host), and `host` is a transport detail, never an identity.
    Before that, `_connections[agent_id] = websocket` meant the second peer to
    connect silently evicted the first and a sleeping laptop traded the slot
    back and forth with the server.

    House Party Protocol is unaffected by any of this: its roster comes from
    the ProfileRegistry (AgentDispatcher.known_agents), never from this table,
    so an agent appears in a broadcast exactly once no matter how many machines
    it is attached from.
    """

    def __init__(self) -> None:
        self._connections: dict[str, dict[str, WebSocket]] = {}
        self._peers: dict[str, dict[str, PeerInfo]] = {}

    async def connect(
        self,
        agent_id: str,
        websocket: WebSocket,
        *,
        host: str = DEFAULT_HOST,
        platform: str = LINUX,
        roots: tuple[str, ...] = (),
    ) -> None:
        self._connections.setdefault(agent_id, {})[host] = websocket
        self._peers.setdefault(agent_id, {})[host] = PeerInfo(
            agent_id=agent_id, host=host, platform=platform, roots=tuple(roots),
        )
        logger.info(
            "ws_connect",
            extra={"agent_id": agent_id, "host": host, "platform": platform,
                   "roots": list(roots)},
        )

    async def disconnect(self, agent_id: str, host: str | None = None) -> None:
        """Drop one host's connection, or every host's when none is named.

        A named host matters: one peer going offline must not deregister the
        others, which is what made a reconnecting laptop able to displace the
        server peer.
        """
        if host is None:
            self._connections.pop(agent_id, None)
            self._peers.pop(agent_id, None)
        else:
            self._connections.get(agent_id, {}).pop(host, None)
            self._peers.get(agent_id, {}).pop(host, None)
            if not self._connections.get(agent_id):
                self._connections.pop(agent_id, None)
                self._peers.pop(agent_id, None)
        logger.info("ws_disconnect", extra={"agent_id": agent_id, "host": host})

    async def send(
        self, agent_id: str, message: dict[str, Any], host: str | None = None
    ) -> None:
        websocket = self._socket(agent_id, host)
        if websocket is None:
            logger.warning(
                "ws_send_no_connection",
                extra={"agent_id": agent_id, "host": host},
            )
            return
        try:
            await websocket.send_json(message)
        except Exception as e:
            logger.error(
                "ws_send_error",
                extra={"agent_id": agent_id, "host": host, "error": str(e)},
            )
            await self.disconnect(agent_id, self._resolved_host(agent_id, host))

    def _resolved_host(self, agent_id: str, host: str | None) -> str | None:
        """The host a `host=None` send would actually reach."""
        if host is not None:
            return host
        res = resolve(self.peers(agent_id), None)
        return res.host

    def _socket(self, agent_id: str, host: str | None) -> WebSocket | None:
        hosts = self._connections.get(agent_id)
        if not hosts:
            return None
        if host is not None:
            return hosts.get(host)
        chosen = self._resolved_host(agent_id, None)
        return hosts.get(chosen) if chosen else None

    def is_connected(self, agent_id: str, host: str | None = None) -> bool:
        hosts = self._connections.get(agent_id)
        if not hosts:
            return False
        return host in hosts if host is not None else bool(hosts)

    def peers(self, agent_id: str) -> list[PeerInfo]:
        """Every machine this agent is attached from, for routing decisions."""
        return list(self._peers.get(agent_id, {}).values())

    def hosts(self, agent_id: str) -> list[str]:
        return list(self._connections.get(agent_id, {}).keys())

    def connected_agents(self) -> list[str]:
        """Distinct agent_ids with at least one peer attached.

        Deliberately deduplicated: an agent attached from three machines is one
        agent, and anything that fans out over this list must fan out once.
        """
        return list(self._connections.keys())
