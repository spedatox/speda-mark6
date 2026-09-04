# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Event-loop stall detector.

The backend is one uvicorn process on one event loop, and everything shares it:
every HTTP request, every SSE stream, and every WebSocket — the Flutter client,
the Forge peers, the lot. Any coroutine that occupies the loop without awaiting
freezes all of them at once, and the failure does not look like a stall. It
looks like the network died: uvicorn pings every WebSocket every 20 s and closes
the ones that miss the deadline, so a long-enough block drops every connected
client in the same second. That is precisely how both Forge peers were dying
together, dozens of times a day, while the process itself stayed healthy and
logged nothing at all.

The blocker is not always guessable from the code. `recall_conversations` was
found by measuring (126.8 MB of BLOBs, 6.05 s per call — see
app/skills/semantic_search.py) but there is no reason to assume it was the only
one, and the next one will be found the same way: by measurement, not by
reading. This is the instrument that makes that possible.

It works by sleeping for a known interval and reporting how much longer than
that it actually took. A loop with nothing blocking it overshoots by
milliseconds; an overshoot of seconds is time some other coroutine held the loop
without yielding, and the log line dates it so it can be correlated with
whatever else ran then. It cannot name the culprit — the loop was not running
this task while it was blocked, by definition — but it turns an invisible
failure into a timestamped, measured one, which is the whole difference between
"the peers keep dropping" and a bug you can go and find.

Deliberately cheap: one sleeping task, one subtraction per tick, and a log line
only when the threshold is crossed.
"""

import asyncio
import logging
import time

logger = logging.getLogger(__name__)


async def watch_event_loop(interval_s: float, threshold_s: float) -> None:
    """Log whenever the loop was blocked for longer than `threshold_s`.

    Runs until cancelled. Never raises into the lifespan: a diagnostic that can
    take the process down is worse than no diagnostic.
    """
    loop = asyncio.get_running_loop()
    while True:
        before = loop.time()
        try:
            await asyncio.sleep(interval_s)
        except asyncio.CancelledError:
            return
        lag = loop.time() - before - interval_s
        if lag < threshold_s:
            continue
        try:
            logger.warning(
                "event_loop_stalled",
                extra={
                    "lag_s": round(lag, 2),
                    # The ping deadline this is really about: past it, uvicorn
                    # starts closing WebSockets and every client reconnects.
                    "ws_ping_deadline_s": 20,
                    "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                },
            )
        except Exception:  # noqa: BLE001 — logging must never break the watchdog
            pass
