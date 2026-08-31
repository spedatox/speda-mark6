# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Live Legion (Task) run visibility — BACKGROUND legionnaires only.

Inline dispatches need no endpoints here: their progress rides the parent
turn's own SSE stream, and reattaches for free via the existing
`/chat/attach/{request_id}` path (their SUBAGENT events are just more
SSEEvents in that turn's own TurnRegistry buffer). A background legionnaire
has no such turn to ride — it detaches into its own asyncio.Task — so it gets
its own tiny attach surface here, backed by
`CapabilityRegistry.legion_runs` (a `LegionRunRegistry`, see
app/legion/run_registry.py). Zero business logic, per Rule 1 — both endpoints
just forward to the registry.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.routers.chat import SSE_HEADERS, _with_keepalive
from app.schemas.sse import SSEEvent, SSEEventType

logger = logging.getLogger(__name__)
router = APIRouter(tags=["legion"])


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
