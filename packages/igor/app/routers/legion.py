# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Live background-run visibility — background legionnaires AND background
dispatches to external peers, plus durable inline/background worker controls.

Both stream into one `LegionRunRegistry` (see its module docstring for why
sharing it is safe), so both are listed by `/legion/active` and tailed by
`/legion/attach/{ticket}`. The path keeps the `legion` prefix because clients
already call it; what it serves is "background work", not Legion alone.

Inline dispatches need no endpoints here: their progress rides the parent
turn's own SSE stream, and reattaches for free via the existing
`/chat/attach/{request_id}` path (their SUBAGENT events are just more
SSEEvents in that turn's own TurnRegistry buffer). A background legionnaire
has no such turn to ride — it detaches into its own asyncio.Task — so it gets
its own tiny attach surface here, backed by
`CapabilityRegistry.legion_runs` (a `LegionRunRegistry`, see
app/legion/run_registry.py). Zero business logic, per Rule 1 — both endpoints
just forward to the registry. The /legion/executions routes use the app-owned
WorkerControlService and survive replay-buffer eviction. Existing ticket
routes remain the compatibility surface for external peer consumers.
"""

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from fastapi.responses import Response, StreamingResponse

from app.routers.chat import SSE_HEADERS, _with_keepalive
from app.schemas.sse import SSEEvent, SSEEventType

logger = logging.getLogger(__name__)
router = APIRouter(tags=["legion"])


class WorkerMessageBody(BaseModel):
    text: str = Field(min_length=1, max_length=16000)
    message_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


@router.get("/legion/executions")
async def worker_executions(request: Request, session_id: int | None = None, agent_id: str | None = None):
    return await request.app.state.worker_control.list(user_id=1, session_id=session_id, agent_id=agent_id)


@router.get("/legion/executions/{execution_id}")
async def worker_execution(execution_id: str, request: Request):
    return await request.app.state.worker_control.inspect(execution_id, user_id=1)


@router.get("/legion/executions/{execution_id}/events")
async def worker_events(execution_id: str, request: Request, after: int = 0):
    return await request.app.state.worker_control.events(execution_id, user_id=1, after=after)


@router.post("/legion/executions/{execution_id}/interrupt")
async def interrupt_worker(execution_id: str, request: Request):
    return await request.app.state.worker_control.interrupt(execution_id, user_id=1)


@router.get("/legion/executions/{execution_id}/event-artifacts/{digest}")
async def worker_event_artifact(execution_id: str, digest: str, request: Request):
    data = await request.app.state.worker_control.event_artifact(execution_id, digest, user_id=1)
    return Response(data, media_type="application/json")


@router.post("/legion/executions/{execution_id}/messages")
async def message_worker(execution_id: str, body: WorkerMessageBody, request: Request):
    return await request.app.state.worker_control.message(execution_id, body.text, user_id=1, message_id=body.message_id)


@router.get("/legion/executions/{execution_id}/attach")
async def attach_worker(execution_id: str, request: Request, after: int = 0):
    service = request.app.state.worker_control
    snapshot = await service.events(execution_id, user_id=1, after=after)

    async def stream():
        async for event in service.subscribe(execution_id, user_id=1, after=after):
            yield SSEEvent(type=SSEEventType.SUBAGENT, data=event,
                           session_id=snapshot["execution"]["session_id"], request_id=execution_id).to_sse()
    return StreamingResponse(_with_keepalive(stream()), media_type="text/event-stream", headers=SSE_HEADERS)


@router.get("/legion/active")
async def legion_active(request: Request, session_id: int | None = None):
    """Background legionnaires currently running — optionally filtered by
    session. The client polls this to discover a ticket to attach to, the
    same shape /chat/active already gives detached chat turns."""
    runs = request.app.state.registry.legion_runs
    if runs is None:
        return []
    return runs.active(session_id=session_id)


@router.get("/legion/attach/{ticket}")
async def legion_attach(ticket: int, request: Request):
    """Attach to a background legionnaire's live progress: replays whatever
    it has already reported, then tails until it finishes. An unknown or
    already-evicted ticket yields an empty stream — its result is already in
    the DB (legion_status / the eventual report turn), so the client just
    shows the finished state normally."""
    runs = request.app.state.registry.legion_runs

    async def _wrap():
        if runs is None:
            return
        session_id = runs.room_session_id(ticket) or 0
        async for event in runs.subscribe(ticket):
            yield SSEEvent(
                type=SSEEventType.SUBAGENT,
                data=event,
                session_id=session_id,
                request_id=f"legion-bg-{ticket}",
            ).to_sse()

    return StreamingResponse(
        _with_keepalive(_wrap()), media_type="text/event-stream", headers=SSE_HEADERS,
    )
