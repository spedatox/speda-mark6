# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The event-loop stall detector must fire on a real block and stay quiet otherwise.

Everything on this backend shares one event loop, and uvicorn closes any
WebSocket that misses its 20 s ping deadline — so a coroutine that blocks the
loop drops every client at once and logs nothing. This watchdog is what turns
that invisible failure into a dated, measured line.
"""

import asyncio
import logging
import time

import pytest

from app.services.loop_monitor import watch_event_loop


def _stalls(caplog):
    return [r for r in caplog.records if r.getMessage() == "event_loop_stalled"]


@pytest.mark.asyncio
async def test_a_blocked_loop_is_reported(caplog):
    with caplog.at_level(logging.WARNING, logger="app.services.loop_monitor"):
        task = asyncio.create_task(watch_event_loop(0.01, 0.15))
        await asyncio.sleep(0)
        time.sleep(0.3)          # block the loop the way real sync work does
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    found = _stalls(caplog)
    assert found, "a 0.3 s block past a 0.15 s threshold must be reported"
    assert found[0].lag_s >= 0.15


@pytest.mark.asyncio
async def test_a_healthy_loop_stays_quiet(caplog):
    with caplog.at_level(logging.WARNING, logger="app.services.loop_monitor"):
        task = asyncio.create_task(watch_event_loop(0.01, 0.5))
        await asyncio.sleep(0.1)  # yielding normally the whole time
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert not _stalls(caplog), "an unblocked loop must log nothing"


@pytest.mark.asyncio
async def test_cancellation_ends_it_cleanly():
    """It is cancelled in the lifespan's shutdown; that must not raise."""
    task = asyncio.create_task(watch_event_loop(0.01, 10.0))
    await asyncio.sleep(0.02)
    task.cancel()
    await asyncio.wait_for(task, timeout=1.0)   # returns, does not propagate
    assert task.done() and not task.cancelled()
