"""
Detached turn runner — the survivability core (BgOps Phase 1).

The problem it solves: persistence used to live inside the SSE generator, so a
client disconnect (renderer reload, app close, watchdog abort) cancelled the
orchestrator mid-flight and the turn was lost. Here, a turn runs in its OWN
asyncio task, detached from any HTTP request. Events land in a bounded replay
buffer and fan out to live subscribers; the run persists its result and fires
post-turn work whether or not anyone is listening.

Contract:
  - start()      — launch a detached turn, return its request_id.
  - subscribe()  — replay the buffer, then tail live events. Unsubscribing (a
                   dropped connection) NEVER affects the run.
  - active()     — list running turns (for re-attach discovery).
  - wait()       — block until a turn has settled (not used by the app itself).
  - cancel()     — the ONLY thing that stops a run; persists the partial answer.

One instance lives on app.state.turns (Rule 6), created in the lifespan.
"""

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import AsyncIterator, Awaitable, Callable

from app.core.context import AgentContext
from app.database import AsyncSessionLocal
from app.schemas.sse import SSEEvent, SSEEventType

logger = logging.getLogger(__name__)

# Engine factory: given a context (with the runner's DB session already set),
# returns the event stream to consume (orchestrator.run or agent_proxy.run).
EngineFactory = Callable[[AgentContext], AsyncIterator[SSEEvent]]

_BUFFER_CAP = 4000          # events kept for replay (bounded — a late re-attach
                            # may miss the very start of a huge stream)
_GRACE_S = 60.0             # keep a finished turn attachable this long, then evict
_MAX_ACTIVE = 8             # concurrent detached turns before new starts are refused


class _Done:
    """Sentinel pushed to subscriber queues when a turn ends."""


_DONE = _Done()


def _failure_marker(note: str | None) -> str:
    """The trailing marker stamped onto a turn that ended in error.

    Mirrors the cancel path's ``_[cancelled by owner]_``. Deterministic given
    the stored note, so the reloaded transcript stays byte-stable and the
    prompt cache holds. The note is included so the next turn can see WHY the
    previous attempt broke off, not just that it did."""
    detail = f": {note.strip()}" if note and note.strip() else ""
    return f"\n\n_[turn ended early — error{detail}]_"


@dataclass
class _Turn:
    request_id: str
    agent_id: str
    session_id: int
    started_at: float
    buffer: deque = field(default_factory=lambda: deque(maxlen=_BUFFER_CAP))
    subscribers: set = field(default_factory=set)   # set[asyncio.Queue]
    done: bool = False
    finished_at: float | None = None
    last_event_at: float = field(default_factory=time.monotonic)
    task: asyncio.Task | None = None


class TurnRegistry:
    def __init__(self, session_manager) -> None:
        self._session_manager = session_manager
        self._turns: dict[str, _Turn] = {}

    # ── Launch ────────────────────────────────────────────────────────────────

    def start(
        self,
        *,
        context: AgentContext,
        engine_factory: EngineFactory,
        format_error: Callable[[Exception], str],
        on_complete: Callable[[], Awaitable[None]] | None = None,
        on_settle: Callable[[str], Awaitable[None]] | None = None,
    ) -> str | None:
        """Launch a detached turn. Returns its request_id, or None if the active
        cap is hit (the caller surfaces a friendly error to the user)."""
        active = sum(1 for t in self._turns.values() if not t.done)
        if active >= _MAX_ACTIVE:
            logger.warning("turn_registry_full", extra={"active": active})
            return None
        turn = _Turn(
            request_id=context.request_id,
            agent_id=context.agent_id,
            session_id=context.session_id,
            started_at=time.monotonic(),
        )
        self._turns[context.request_id] = turn
        turn.task = asyncio.create_task(
            self._run(turn, context, engine_factory, format_error, on_complete, on_settle)
        )
        return context.request_id

    # ── The detached run ──────────────────────────────────────────────────────

    async def _run(
        self,
        turn: _Turn,
        context: AgentContext,
        engine_factory: EngineFactory,
        format_error: Callable[[Exception], str],
        on_complete: Callable[[], Awaitable[None]] | None,
        on_settle: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        chunks: list[str] = []
        tools: list[dict] = []
        files: list[dict] = []
        cancelled = False
        failed = False          # a terminal error (graceful or raised) ended the turn
        failure_note: str | None = None  # the error text, stamped into the saved turn
        terminal_seen = False  # engine emitted a DONE/ERROR of its own
        running_len = 0  # chars streamed so far — stamped onto each tool as
        # afterChars so a reloaded/historical message can interleave tools at
        # the point they actually fired instead of always stacking them before
        # the text (Heartbreaker's Message.tsx reads this). Mirrors the same
        # counter the live SSE client keeps independently.
        try:
            # Own DB session — the request's session is long gone once the HTTP
            # response returns; the engine and persistence must use this one.
            async with AsyncSessionLocal() as db:
                context.db = db
                try:
                    async for event in engine_factory(context):
                        self._emit(turn, event)
                        et = event.type
                        if et in (SSEEventType.DONE, SSEEventType.ERROR):
                            terminal_seen = True
                        if et == SSEEventType.ERROR:
                            # A graceful terminal error from the engine/peer. Note
                            # it so the partial turn is persisted with a marker
                            # instead of vanishing from the history.
                            failed = True
                            failure_note = str(event.data) if event.data else None
                        if et == SSEEventType.CHUNK and isinstance(event.data, str):
                            chunks.append(event.data)
                            running_len += len(event.data)
                        elif et == SSEEventType.TOOL:
                            d = event.data if isinstance(event.data, dict) else {}
                            tools.append({
                                "id": d.get("id"), "name": d.get("name"), "input": d.get("input"),
                                "afterChars": running_len,
                            })
                        elif et == SSEEventType.TOOL_RESULT:
                            d = event.data if isinstance(event.data, dict) else {}
                            for t in tools:
                                if t.get("id") == d.get("id"):
                                    t["result"] = d.get("result")
                                    break
                        elif et == SSEEventType.FILE:
                            files.append(event.data)
                except asyncio.CancelledError:
                    cancelled = True
                    chunks.append("\n\n_[cancelled by owner]_")
                    self._emit(turn, SSEEvent(
                        type=SSEEventType.DONE, data="".join(chunks),
                        session_id=turn.session_id, request_id=turn.request_id,
                    ))
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "turn_stream_failed",
                        extra={"request_id": turn.request_id, "error": str(exc)},
                    )
                    failed = True
                    failure_note = format_error(exc)
                    terminal_seen = True    # we emit ERROR below; no synthetic DONE
                    self._emit(turn, SSEEvent(
                        type=SSEEventType.ERROR, data=failure_note,
                        session_id=turn.session_id, request_id=turn.request_id,
                    ))
                    # Fall through to persist the partial turn. A turn that died
                    # after doing real work must leave a trace — otherwise the
                    # next turn starts blind, as if the work never happened.

                # Guaranteed terminal: if the engine generator completed without
                # ever emitting DONE/ERROR (the external-proxy path can), inject
                # a synthetic DONE so any subscriber — live or reattaching —
                # always settles instead of hanging on "Reconnecting".
                if not terminal_seen and not cancelled:
                    logger.warning(
                        "turn_missing_terminal",
                        extra={"request_id": turn.request_id},
                    )
                    self._emit(turn, SSEEvent(
                        type=SSEEventType.DONE, data={},
                        session_id=turn.session_id, request_id=turn.request_id,
                    ))

                # A failed turn is stamped so the reloaded transcript shows it
                # broke off — a half-finished answer must not read as complete,
                # and the marker guarantees there is something to persist even
                # when no text streamed before the failure.
                if failed:
                    chunks.append(_failure_marker(failure_note))

                # Persist the assistant turn (moved verbatim out of the router's
                # SSE generator — it now runs regardless of who is listening).
                await self._persist(db, turn, chunks, tools, files)

                # Token spend for this turn, on the session's running totals.
                # Runs on every outcome including a failed or cancelled turn —
                # tokens burned before the failure were still billed, and a
                # counter that quietly omits them is worse than none.
                spend = context.extra.get("token_usage") or {}
                if spend:
                    try:
                        await self._session_manager.add_token_usage(
                            db, turn.session_id,
                            int(spend.get("input", 0)), int(spend.get("output", 0)),
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.warning("turn_usage_persist_failed",
                                       extra={"request_id": turn.request_id, "error": str(e)})

            # Post-turn work (title/log/compaction/embedding) — detached, after
            # persistence, never blocking the stream (Rule 7). Skipped on cancel
            # or failure so a half-turn doesn't get titled/embedded as if complete.
            if on_complete is not None and not cancelled and not failed:
                try:
                    await on_complete()
                except Exception as e:  # noqa: BLE001
                    logger.warning("turn_on_complete_failed", extra={"request_id": turn.request_id, "error": str(e)})

            # Settle hook — unlike on_complete this runs on EVERY outcome. For a
            # triggered run it is the delivery step: a briefing that broke off
            # half-way must still reach the owner (with the failure marker the
            # persisted turn carries), which is what the pre-runner trigger path
            # did. Chat passes none.
            if on_settle is not None:
                status = "cancelled" if cancelled else ("failed" if failed else "ok")
                try:
                    await on_settle(status)
                except Exception as e:  # noqa: BLE001
                    logger.warning("turn_on_settle_failed", extra={"request_id": turn.request_id, "error": str(e)})
        finally:
            await self._finish(turn)

    async def _persist(self, db, turn: _Turn, chunks: list[str], tools: list[dict], files: list[dict]) -> None:
        full = "".join(chunks)
        # A turn that ran tools counts as work even with no text — dropping it
        # would erase what the agent did from the next turn's history.
        if not (full or files or tools):
            return
        content: list = [{"type": "text", "text": full}]
        if tools or files:
            content.append({"type": "_speda_meta", "tools": tools, "files": files})
        try:
            await self._session_manager.save_message(db, turn.session_id, "assistant", content)
        except Exception as e:  # noqa: BLE001
            logger.error("turn_persist_failed", extra={"request_id": turn.request_id, "error": str(e)})

    def _emit(self, turn: _Turn, event: SSEEvent) -> None:
        """Buffer an event and fan it out to live subscribers. Synchronous — no
        await between buffer append and fanout, so subscribe()'s atomic snapshot
        can never double-count or miss an event."""
        turn.buffer.append(event)
        turn.last_event_at = time.monotonic()
        for q in turn.subscribers:
            q.put_nowait(event)

    async def _finish(self, turn: _Turn) -> None:
        if turn.done:
            return
        turn.done = True
        turn.finished_at = time.monotonic()
        for q in turn.subscribers:
            q.put_nowait(_DONE)
        asyncio.create_task(self._evict_later(turn.request_id))

    async def _evict_later(self, request_id: str) -> None:
        await asyncio.sleep(_GRACE_S)
        self._turns.pop(request_id, None)

    # ── Subscribe (replay + live tail) ────────────────────────────────────────

    async def subscribe(self, request_id: str) -> AsyncIterator[str]:
        """Yield SSE wire strings: the buffered replay, then live events until the
        turn ends. A dropped connection just stops iterating — it NEVER cancels
        the run (that is what cancel() is for)."""
        turn = self._turns.get(request_id)
        if turn is None:
            return  # evicted / unknown — router returns 404 for the bare attach

        if turn.done:
            for event in list(turn.buffer):
                yield event.to_sse()
            return

        q: asyncio.Queue = asyncio.Queue()
        # Atomic (no await between): snapshot the buffer and register the queue.
        # Events already buffered are replayed; events after this point arrive
        # via the queue — never both.
        snapshot = list(turn.buffer)
        turn.subscribers.add(q)
        try:
            for event in snapshot:
                yield event.to_sse()
            while True:
                item = await q.get()
                if item is _DONE:
                    break
                yield item.to_sse()
        finally:
            turn.subscribers.discard(q)

    # ── Introspection + control ───────────────────────────────────────────────

    def active(self, *, agent_id: str | None = None, session_id: int | None = None) -> list[dict]:
        now = time.monotonic()
        out = []
        for t in self._turns.values():
            if t.done:
                continue
            if agent_id is not None and t.agent_id != agent_id:
                continue
            if session_id is not None and t.session_id != session_id:
                continue
            out.append({
                "request_id": t.request_id,
                "agent_id": t.agent_id,
                "session_id": t.session_id,
                "running_s": round(now - t.started_at, 1),
                "idle_s": round(now - t.last_event_at, 1),
            })
        return out

    async def wait(self, request_id: str, *, timeout: float | None = None) -> bool:
        """Block until a turn has fully settled — result persisted, post-turn work
        done, settle hook run. Returns False if the id is unknown (never started,
        or already evicted past the grace window), True once the run has ended.
        Raises TimeoutError if the turn outlives `timeout`.

        The app never waits on a detached turn — not waiting is the whole point.
        This exists for anything that genuinely needs the END of a run (tests,
        tooling): a check that only passes when it wins a race against the
        registry is worse than no check. The task is shielded, so a timeout here
        does not stop the run — cancel() remains the only thing that does.
        """
        turn = self._turns.get(request_id)
        if turn is None:
            return False
        if turn.task is not None and not turn.done:
            await asyncio.wait_for(asyncio.shield(turn.task), timeout)
        return True

    async def cancel(self, request_id: str) -> bool:
        """Cancel a running turn. The run's CancelledError handler persists the
        partial answer with a marker before finishing. Returns True if a live
        turn was cancelled."""
        turn = self._turns.get(request_id)
        if turn is None or turn.done or turn.task is None:
            return False
        turn.task.cancel()
        return True

    async def shutdown(self) -> None:
        """Cancel every in-flight turn on app shutdown."""
        tasks = [t.task for t in self._turns.values() if t.task is not None and not t.done]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
