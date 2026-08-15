"""
The host bridge — how anything inside the container reaches the REAL host.

In production the backend runs inside a container, so a local subprocess only
ever sees the container's namespace. When settings.system_ops_host is set (e.g.
"root@host.docker.internal"), commands are routed over SSH to the actual host
across the Docker BRIDGE network — deliberately not the public NIC.

That distinction is what makes the Lockdown Protocol reversible: lockdown drops
external traffic to the public SSH port while this bridge path keeps working, so
the host stays operable (and lockdown liftable) from inside.

Extracted from skills/system_ops.py so the lockdown service can use the same
channel without going through that skill's orion/optimus-only restriction.
"""

import asyncio
import logging
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)


def remote_enabled() -> bool:
    """True when commands must run on the real host over SSH (prod-in-container)."""
    return bool((settings.system_ops_host or "").strip())


def ssh_argv(remote_command: str) -> list[str]:
    """argv for one SSH invocation of `remote_command` on the host.

    BatchMode → never hangs on a prompt (a missing/rejected key fails loud and
    fast). accept-new pins the host key on first contact into a per-deployment
    known_hosts inside the data dir, so a later host-key change — a MITM signal
    — is refused, not re-accepted.
    """
    key = settings.system_ops_ssh_key
    known_hosts = str(Path(key).parent / "host_ops_known_hosts")
    return [
        "ssh",
        "-i", key,
        "-p", str(settings.system_ops_ssh_port),
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", f"UserKnownHostsFile={known_hosts}",
        "-o", "ConnectTimeout=10",
        settings.system_ops_host.strip(),
        "--",
        remote_command,
    ]


async def run(command: str, timeout: int = 30) -> tuple[int, str, str]:
    """Run `command` wherever the host actually is: over the SSH bridge when
    remote_enabled(), otherwise in this process's own shell (dev/bare metal).

    Returns (exit_code, stdout, stderr). A transport failure comes back as
    exit code 255 with the reason in stderr rather than raising, so callers can
    treat "could not reach the host" and "the command failed" the same way.
    """
    try:
        if remote_enabled():
            proc = await asyncio.create_subprocess_exec(
                *ssh_argv(command),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        else:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return 255, "", f"command timed out after {timeout}s and was killed"
    except Exception as e:  # noqa: BLE001
        logger.error("host_bridge_failed", extra={"error": str(e)})
        return 255, "", str(e)

    return (
        proc.returncode or 0,
        (stdout or b"").decode("utf-8", "replace"),
        (stderr or b"").decode("utf-8", "replace"),
    )
