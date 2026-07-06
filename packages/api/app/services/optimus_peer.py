"""
Optimus peer launcher — SPEDA owns the peer's lifecycle.

When `optimus_peer_dir` is configured, the lifespan handler starts the
standalone Optimus peer (`python -m optimus.peer`) as a child process right
after the backend is up, and stops it at shutdown. The peer then connects back
to this backend's agents WebSocket like any external peer — no separate
terminal, no manual plumbing. Env the peer needs (API key, WS URL, workspace)
is injected here from settings, so .env on the SPEDA side is the single place
to configure the whole network.

Startup is best-effort by design: a missing directory, a broken python, or an
instant crash logs a warning and SPEDA keeps running — the in-process Optimus
profile remains the fallback, exactly as when the peer is offline.
"""

import asyncio
import logging
import os
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

_STOP_GRACE_S = 5.0


class OptimusPeerLauncher:
    """Starts/stops the Optimus peer child process. One instance on app.state."""

    def __init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        peer_dir = (settings.optimus_peer_dir or "").strip()
        if not settings.optimus_peer_autostart or not peer_dir:
            logger.info("optimus_peer_autostart_disabled")
            return
        if not (Path(peer_dir) / "optimus" / "peer" / "__main__.py").exists():
            logger.warning(
                "optimus_peer_dir_invalid",
                extra={"dir": peer_dir, "hint": "expected <dir>/optimus/peer/__main__.py"},
            )
            return

        python = (settings.optimus_peer_python or "").strip() or "python"
        env = {
            **os.environ,
            "SPEDA_API_KEY": settings.speda_api_key,
            "SPEDA_WS_URL": settings.optimus_peer_ws_url,
            # The peer's workspace defaults to its own repo unless overridden.
            "OPTIMUS_WORKSPACE": os.environ.get("OPTIMUS_WORKSPACE", peer_dir),
        }
        try:
            # stdout/stderr inherit the SPEDA console — the peer's log lines
            # appear alongside the backend's, one terminal for the network.
            self._proc = await asyncio.create_subprocess_exec(
                python, "-m", "optimus.peer",
                cwd=peer_dir,
                env=env,
            )
        except (OSError, NotImplementedError) as e:
            logger.warning(
                "optimus_peer_start_failed",
                extra={"dir": peer_dir, "python": python, "error": str(e)},
            )
            self._proc = None
            return
        logger.info(
            "optimus_peer_started",
            extra={"pid": self._proc.pid, "dir": peer_dir, "ws": settings.optimus_peer_ws_url},
        )

    async def stop(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=_STOP_GRACE_S)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
            logger.info("optimus_peer_stopped", extra={"returncode": proc.returncode})
        except ProcessLookupError:
            pass  # already gone
        except Exception as e:  # noqa: BLE001 — shutdown must complete regardless
            logger.warning("optimus_peer_stop_failed", extra={"error": str(e)})
