"""
Brute-force throttle for the login endpoint.

In-memory, per-key (client IP) failed-attempt counter with a sliding window and
lockout. The backend is single-owner and runs as one process, so a process-local
limiter is sufficient and has zero dependencies — no Redis, no DB row per attempt.
A successful login clears the key; reaching the cap locks it for `lockout_s`.

This is defence in depth on top of scrypt: it turns an online password-guessing
attack from "thousands per second" into "a handful per 15 minutes".
"""

import threading
import time


class LoginRateLimiter:
    def __init__(self, max_attempts: int = 5, window_s: int = 900, lockout_s: int = 900) -> None:
        self._max = max_attempts
        self._window = window_s
        self._lockout = lockout_s
        self._lock = threading.Lock()
        # key -> (failure_timestamps, locked_until)
        self._state: dict[str, tuple[list[float], float]] = {}

    def check(self, key: str) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds). allowed=False means locked out."""
        now = time.monotonic()
        with self._lock:
            fails, locked_until = self._state.get(key, ([], 0.0))
            if locked_until > now:
                return False, int(locked_until - now) + 1
            return True, 0

    def record_failure(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            fails, _ = self._state.get(key, ([], 0.0))
            fails = [t for t in fails if now - t < self._window]
            fails.append(now)
            locked_until = now + self._lockout if len(fails) >= self._max else 0.0
            self._state[key] = (fails, locked_until)

    def reset(self, key: str) -> None:
        with self._lock:
            self._state.pop(key, None)
