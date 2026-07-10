"""Mark VI sandbox launcher — local mode.

The sandbox is the isolated computer SPEDA runs commands in (the `run_command`
skill posts to `settings.sandbox_url`). In production it is a Docker service on
Contabo (`docker-compose.yml`, internal network only). On a dev machine without
Docker there is nothing to answer those requests, so this launcher spawns the
stdlib exec server (`packages/sandbox/server.py`) as a child process bound to a
local workspace directory.

It only spawns when:
  - `sandbox_autostart` is on, and
  - `sandbox_url` points at localhost/127.0.0.1, and
  - nothing already answers GET /health there.

That last check means a running Docker sandbox — or a manually started one —
always wins; the launcher never double-binds the port. This is honestly reduced
isolation (a workspace jail, not a container); Docker remains the production
isolation. The server itself is stdlib-only and cross-platform, so it needs no
changes to run here.
"""

import asyncio
import logging
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_STOP_GRACE_S = 5.0
# packages/sandbox/server.py, resolved from this file:
# app/services/sandbox_launcher.py → parents[3] == packages/api ; the sandbox is
# a sibling package under packages/.
_SANDBOX_SERVER = (
    Path(__file__).resolve().parents[3] / "sandbox" / "server.py"
)


class SandboxLauncher:
    """Starts/stops the local sandbox exec server. One instance on app.state."""

    def __init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None

    @staticmethod
    def _is_local(url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return host in ("localhost", "127.0.0.1", "0.0.0.0")

    async def _already_running(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(f"{settings.sandbox_url}/health")
                return resp.status_code == 200
        except Exception:  # noqa: BLE001 — any failure means "not answering"
            return False

    async def start(self) -> None:
        if not settings.sandbox_autostart or not settings.sandbox_url:
            logger.info("sandbox_autostart_disabled")
            return
        if not self._is_local(settings.sandbox_url):
            logger.info("sandbox_url_remote", extra={"url": settings.sandbox_url})
            return
        if await self._already_running():
            logger.info("sandbox_already_running", extra={"url": settings.sandbox_url})
            return
        if not _SANDBOX_SERVER.exists():
            logger.warning("sandbox_server_missing", extra={"path": str(_SANDBOX_SERVER)})
            return

        workspace = settings.sandbox_workspace
        Path(workspace).mkdir(parents=True, exist_ok=True)
        env = {
            **os.environ,
            "SANDBOX_PORT": str(settings.sandbox_local_port),
            "SANDBOX_WORKSPACE": workspace,
        }
        try:
            # stdout/stderr inherit the SPEDA console (one terminal for the boot).
            self._proc = await asyncio.create_subprocess_exec(
                sys.executable, str(_SANDBOX_SERVER),
                env=env,
            )
        except (OSError, NotImplementedError) as e:
            logger.warning(
                "sandbox_start_failed",
                extra={"path": str(_SANDBOX_SERVER), "error": str(e)},
            )
            self._proc = None
            return
        logger.info(
            "sandbox_started",
            extra={
                "pid": self._proc.pid,
                "port": settings.sandbox_local_port,
                "workspace": workspace,
            },
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
            logger.info("sandbox_stopped", extra={"returncode": proc.returncode})
        except ProcessLookupError:
            pass  # already gone
        except Exception as e:  # noqa: BLE001 — shutdown must complete regardless
            logger.warning("sandbox_stop_failed", extra={"error": str(e)})
