# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Live progress registry for BACKGROUND work — Legion workers and dispatched
external peer jobs alike.

It is named for Legion because that is what first needed it, but it is not
Legion-specific: `AgentDispatcher` shares this same instance for background
dispatches (app/core/dispatch.py). That is safe because both mint their ticket
from the same `AgentMessage` row id, so the two id spaces cannot collide, and
it is what lets a spawned Optimus job appear in the tray and attach over
`/legion/attach/{ticket}` with no second endpoint and no client change.

Mirrors `app/core/turn_runner.py`'s `TurnRegistry` — a ring buffer per run plus
subscriber queues, snapshot-then-tail semantics on attach — but scoped to
Legion tickets. A legionnaire is not an LLM turn: it has no cancel/steer
target and no db-backed persistence, so it must not share `TurnRegistry`'s
semantics or its client-facing endpoints. INLINE legionnaires need none of
this at all — their SUBAGENT events are just more `SSEEvent`s on the parent
turn's own `TurnRegistry` buffer, replayed for free by the existing
`/chat/attach/{request_id}` reattach path.
"""

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import AsyncGenerator

_DONE = object()
_BUFFER_SIZE = 500
_GRACE_S = 60.0


@dataclass
class _Run:
    agent: str
    label: str
    room_session_id: int
    started_at: float = field(default_factory=time.monotonic)
    buffer: deque = field(default_factory=lambda: deque(maxlen=_BUFFER_SIZE))
    subscribers: set = field(default_factory=set)
    done: bool = False


class LegionRunRegistry:
    """One instance, owned by `LegionRunner` (`self.runs`)."""

    def __init__(self) -> None:
        self._runs: dict[int, _Run] = {}

    def register(self, ticket: int, *, agent: str, label: str, room_session_id: int) -> None:
        self._runs[ticket] = _Run(agent=agent, label=label, room_session_id=room_session_id)

    def emit(self, ticket: int, event: dict) -> None:
        """Buffer + fan out, synchronously — no `await` between the two, the
        same atomicity `TurnRegistry._emit` relies on so `subscribe()`'s
        snapshot-then-tail never drops or duplicates an event."""
        run = self._runs.get(ticket)
        if run is None or run.done:
            return
        run.buffer.append(event)
        for q in run.subscribers:
            q.put_nowait(event)

    def finish(self, ticket: int, *, ok: bool) -> None:
        run = self._runs.get(ticket)
        if run is None or run.done:
            return
        run.done = True
        for q in run.subscribers:
            q.put_nowait(_DONE)
        try:
            asyncio.get_running_loop().call_later(_GRACE_S, self._evict, ticket)
        except RuntimeError:
            # No running loop (e.g. torn down during shutdown) — nothing left
            # to evict for, since the process is exiting anyway.
            pass

    def _evict(self, ticket: int) -> None:
        self._runs.pop(ticket, None)

    def room_session_id(self, ticket: int) -> int | None:
        """The session a ticket's completion reports back into — the router
        needs this to build the SSEEvent envelope around each raw event."""
        run = self._runs.get(ticket)
        return run.room_session_id if run is not None else None

    def active(self, session_id: int | None = None) -> list[dict]:
        now = time.monotonic()
        out = []
        for ticket, run in self._runs.items():
            if run.done:
                continue
            if session_id is not None and run.room_session_id != session_id:
                continue
            out.append({
                "ticket": ticket,
                "agent": run.agent,
                "label": run.label,
                "room_session_id": run.room_session_id,
                "running_s": now - run.started_at,
            })
        return out

    async def subscribe(self, ticket: int) -> AsyncGenerator[dict, None]:
        """Unknown ticket → empty stream (404-equivalent at the router).
        Already finished → replay the buffer once. Otherwise: atomically
        snapshot the buffer, register a fresh queue, replay the snapshot,
        then tail live events until `finish()`."""
        run = self._runs.get(ticket)
        if run is None:
            return
        if run.done:
            for event in list(run.buffer):
                yield event
            return

        q: asyncio.Queue = asyncio.Queue()
        snapshot = list(run.buffer)
        run.subscribers.add(q)
        try:
            for event in snapshot:
                yield event
            while True:
                event = await q.get()
                if event is _DONE:
                    break
                yield event
        finally:
            run.subscribers.discard(q)
