"""
system_ops — the host operation skill (docs/MEMORY_ARCHITECTURE.md §3.5).

This is the ONE privileged skill that touches the actual host Mark VI runs on,
as opposed to run_command's isolated sandbox container. It exists so the
custodial agents (Orion primarily, Optimus for infrastructure work) can do real
maintenance — rotate logs, inspect /tmp/speda_outputs, run disk/health checks,
look at why a container is eating RAM — when the owner asks.

HOST BRIDGE: in production the backend itself runs inside a container, so local
execution only ever sees the container's namespace. When
settings.system_ops_host is set (e.g. "root@host.docker.internal"), every
action is routed over SSH to the real host — dedicated restricted key,
BatchMode (never prompts), per-deployment known_hosts — with the same
deny-list, jail, and audit trail, just a different namespace. Empty host =
execute locally, which keeps dev and bare-metal deployments unchanged.

Hard scoping (defence in depth):
  - restricted_to = {"orion", "optimus"}: the registry hides it from every other
    agent and refuses to execute it for anyone else, regardless of allowlist.
  - settings.system_ops_enabled gates it OFF by default — a deployment must opt in.
  - a deny-list blocks obviously catastrophic commands.
  - file writes are jailed under settings.system_ops_root.
  - every invocation is logged (logger + /memories/.audit/ops.md) with request_id.
"""

import asyncio
import logging
import os
import shlex
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from sqlalchemy import select

from app.config import settings
from app.core.context import AgentContext
from app.models.memory_file import MemoryFile
from app.skills.base import Skill

logger = logging.getLogger(__name__)

OPS_LOG_PATH = "/memories/.audit/ops.md"

# Catastrophic patterns refused outright. Not a sandbox — this is the real host,
# so the bar is "could this brick the box or wipe owner data", erring safe.
_DENY_SUBSTRINGS = (
    "rm -rf /",
    "rm -rf /*",
    ":(){",           # fork bomb
    "mkfs",
    "dd if=",
    "> /dev/sd",
    "shutdown",
    "reboot",
    "halt",
    "init 0",
    "init 6",
    "chmod -r 000",
    "chown -r",
    "> /etc/",
    "userdel",
    "passwd ",
)


def _denied(command: str) -> str | None:
    low = command.lower()
    for bad in _DENY_SUBSTRINGS:
        if bad in low:
            return bad
    return None


# Restarting one of these restarts the container the backend (and therefore the
# calling agent) runs inside — it MUST be deferred, or the agent kills its own
# turn mid-reply. Everything else is safe to restart synchronously.
_SELF_SERVICES = {"app", "igor", "speda-app-1", "speda"}


def _remote() -> bool:
    """True when actions must run on the real host over SSH (prod-in-container)."""
    return bool((settings.system_ops_host or "").strip())


def _ssh_argv(remote_command: str) -> list[str]:
    """argv for one SSH invocation of `remote_command` on the host.

    BatchMode → never hangs on a prompt (a missing/rejected key fails loud and
    fast, which the model sees as an error result). accept-new pins the host key
    on first contact into a per-deployment known_hosts inside the data dir, so a
    later host-key change — a MITM signal — is refused, not re-accepted.
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


class SystemOpsSkill(Skill):
    name = "system_ops"
    restricted_to = frozenset({"orion", "optimus"})
    read_only = False
    requires_network = False
    description = (
        "Operate the HOST computer Mark VI runs on — the maintenance hands of Orion "
        "(custodial upkeep) and Optimus (infrastructure work). "
        "Use it for real system upkeep: inspect disk/memory/processes, rotate or read "
        "logs, look inside /tmp/speda_outputs, manage Docker containers (the host's "
        "docker CLI is reachable through it), check why a service is misbehaving, and "
        "read or write maintenance files under the ops root. Do NOT use "
        "it for research, for anything touching the owner's chat data or memory files "
        "(edit those with the `memory` tool), or as a substitute for the isolated "
        "`run_command` sandbox when a task just needs generic compute. Actions: 'exec' "
        "(run a shell command, capped by a timeout), 'read_file' (read a host file), "
        "'write_file' (write a file, confined to the ops root). Returns command output "
        "with exit code, or file contents / a write confirmation."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["exec", "read_file", "write_file", "restart_service"],
                "description": (
                    "exec: run a shell command. read_file: read a host file. "
                    "write_file: write a file under the ops root. restart_service: "
                    "safely restart a container — ALWAYS use this to restart Igor "
                    "(never a raw 'docker restart' on your own container)."
                ),
            },
            "command": {"type": "string", "description": "Shell command for action=exec."},
            "path": {"type": "string", "description": "Absolute host path for read_file/write_file."},
            "content": {"type": "string", "description": "File content for write_file."},
            "service": {
                "type": "string",
                "description": (
                    "For restart_service: which compose service — 'app' (= Igor, "
                    "yourself; scheduled detached so your reply survives), or "
                    "'n8n' / 'sandbox' / 'caddy' (restarted synchronously). Default 'app'."
                ),
            },
            "timeout": {
                "type": "integer",
                "description": f"Max seconds for exec (default 30, hard cap {settings.system_ops_timeout}).",
            },
            "delay": {
                "type": "integer",
                "description": "For a SELF restart_service (app/Igor): seconds to wait so your reply finishes first (default 10, max 60).",
            },
        },
        "required": ["action"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        if not settings.system_ops_enabled:
            return (
                "system_ops is disabled on this deployment (SYSTEM_OPS_ENABLED is off). "
                "Ask the owner to enable it before attempting host maintenance."
            )
        # Belt-and-braces: the registry already blocks other callers, but never
        # trust a single gate for a host-privileged tool.
        if context.agent_id not in ("orion", "optimus"):
            return "system_ops is restricted to Orion and Optimus."

        action = (args.get("action") or "").strip()
        if action == "exec":
            return await self._exec(args, context)
        if action == "read_file":
            return await self._read_file(args, context)
        if action == "write_file":
            return await self._write_file(args, context)
        if action == "restart_service":
            return await self._restart_service(args, context)
        return f"Error: unknown action '{action}'. Valid: exec, read_file, write_file, restart_service."

    # ── Actions ────────────────────────────────────────────────────────────────

    async def _exec(self, args: dict, context: AgentContext) -> str:
        command = (args.get("command") or "").strip()
        if not command:
            return "No command provided."

        bad = _denied(command)
        if bad is not None:
            await self._log_op(context, f"BLOCKED exec ({bad}): {command}")
            return f"Refused: command matches a blocked pattern ('{bad}'). Not executed."

        hard = settings.system_ops_timeout
        timeout = min(int(args.get("timeout", 30) or 30), hard)

        try:
            if _remote():
                # Host bridge: the command string is handed to the HOST's shell via
                # sshd — same trust level as the local shell branch, different
                # namespace. ssh's own exit code passes the remote one through
                # (255 = connection/auth failure, visibly distinct).
                proc = await asyncio.create_subprocess_exec(
                    *_ssh_argv(command),
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
                await self._log_op(context, f"TIMEOUT exec ({timeout}s): {command}")
                return f"Command timed out after {timeout}s and was killed."
        except Exception as e:  # noqa: BLE001
            logger.error("system_ops_exec_failed", extra={"request_id": context.request_id, "error": str(e)})
            return f"Failed to run command: {e}"

        out = (stdout or b"").decode("utf-8", "replace")
        err = (stderr or b"").decode("utf-8", "replace")
        code = proc.returncode
        where = "host" if _remote() else "local"
        await self._log_op(context, f"exec [{where}] (exit={code}): {command}")

        parts = [f"exit_code: {code}"]
        if out:
            parts.append(f"stdout:\n{out[:8000]}")
        if err:
            parts.append(f"stderr:\n{err[:4000]}")
        if not out and not err:
            parts.append("(no output)")
        return "\n\n".join(parts)

    async def _restart_service(self, args: dict, context: AgentContext) -> str:
        """Restart a container safely. For the SELF service (Igor/app) this is the
        one operation an in-process agent cannot do inline — restarting your own
        container mid-turn severs the reply ("pulls a Kurt Cobain"). So a self
        restart is DETACHED on the host and DELAYED: the shell backgrounds
        immediately (this call returns at once, letting the turn finish and
        persist), then the container recycles `delay` seconds later, well after
        the reply has flushed. Other services carry no such hazard and restart
        synchronously with a status read-back."""
        service = (args.get("service") or "app").strip().lower()
        # Resolve the live container by its compose-service label — robust to the
        # project name (speda-app-1 etc.) without hardcoding it.
        svc = "app" if service in _SELF_SERVICES else service
        resolve = f"docker ps -q -f label=com.docker.compose.service={shlex.quote(svc)}"

        if service in _SELF_SERVICES:
            delay = max(3, min(int(args.get("delay", 10) or 10), 60))
            # setsid + & → survives this container dying; sleep → reply flushes first.
            cmd = (
                f"setsid sh -c 'sleep {delay} && docker restart $({resolve})' "
                f">/tmp/speda_restart.log 2>&1 </dev/null &"
            )
            result = await self._exec({"command": cmd, "timeout": 15}, context)
            await self._log_op(context, f"restart_service SELF ({svc}) scheduled +{delay}s")
            return (
                f"Igor ({svc}) restart SCHEDULED in {delay}s — detached on the host, so "
                f"it fires AFTER this turn. Do NOT run any further commands. Write your "
                f"closing report to the owner now; this process recycles once the delay "
                f"elapses. Confirm health on your NEXT message with "
                f"`curl -fsS http://localhost:8000/health`.\n\n{result}"
            )

        # Non-self: safe to do synchronously and read back status in the same turn.
        cmd = (
            f"docker restart $({resolve}) ; sleep 2 ; "
            f"docker ps --filter label=com.docker.compose.service={shlex.quote(svc)}"
        )
        result = await self._exec({"command": cmd, "timeout": 30}, context)
        await self._log_op(context, f"restart_service ({svc})")
        return result

    async def _read_file(self, args: dict, context: AgentContext) -> str:
        raw = (args.get("path") or "").strip()
        if not raw:
            return "No path provided."

        if _remote():
            if not PurePosixPath(raw).is_absolute():
                return "Path must be absolute."
            try:
                proc = await asyncio.create_subprocess_exec(
                    *_ssh_argv(f"cat {shlex.quote(raw)}"),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)
            except asyncio.TimeoutError:
                proc.kill()
                return "Host read timed out and was killed."
            except Exception as e:  # noqa: BLE001
                return f"Could not read {raw} on host: {e}"
            if proc.returncode != 0:
                err = (stderr or b"").decode("utf-8", "replace")[:1000]
                return f"Could not read {raw} on host (exit {proc.returncode}): {err}"
            await self._log_op(context, f"read_file [host]: {raw}")
            return (stdout or b"").decode("utf-8", "replace")[:16000]

        p = Path(raw)
        if not p.is_absolute():
            return "Path must be absolute."
        try:
            data = p.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return f"No such file: {raw}"
        except Exception as e:  # noqa: BLE001
            return f"Could not read {raw}: {e}"
        await self._log_op(context, f"read_file: {raw}")
        return data[:16000]

    async def _write_file(self, args: dict, context: AgentContext) -> str:
        raw = (args.get("path") or "").strip()
        content = args.get("content", "")
        if not raw:
            return "No path provided."

        if _remote():
            return await self._write_file_remote(raw, content, context)

        # Write jail: confine to the ops root subtree, resolved to defeat `..`.
        root = Path(settings.system_ops_root).resolve()
        try:
            target = Path(raw).resolve()
            target.relative_to(root)
        except ValueError:
            return f"Refused: writes are confined to {root}. '{raw}' is outside it."

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            return f"Could not write {target}: {e}"
        await self._log_op(context, f"write_file ({len(content)} bytes): {target}")
        return f"Wrote {len(content)} bytes to {target}."

    async def _write_file_remote(self, raw: str, content: str, context: AgentContext) -> str:
        """Jailed write on the HOST. The jail is enforced on the normalized POSIX
        path (absolute, no `..` segments, under the ops root) — we cannot resolve
        symlinks on a remote filesystem, so `..` is rejected outright instead.
        Content travels via stdin, so it is never shell-interpreted."""
        target = PurePosixPath(raw)
        root = PurePosixPath(settings.system_ops_root)
        if not target.is_absolute():
            return "Path must be absolute."
        if ".." in target.parts:
            return "Refused: '..' segments are not allowed in host write paths."
        try:
            target.relative_to(root)
        except ValueError:
            return f"Refused: writes are confined to {root}. '{raw}' is outside it."

        remote_cmd = (
            f"mkdir -p {shlex.quote(str(target.parent))} && "
            f"cat > {shlex.quote(str(target))}"
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                *_ssh_argv(remote_cmd),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(
                proc.communicate(input=content.encode("utf-8")),
                timeout=settings.system_ops_timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            return "Host write timed out and was killed."
        except Exception as e:  # noqa: BLE001
            return f"Could not write {target} on host: {e}"

        if proc.returncode != 0:
            err = (stderr or b"").decode("utf-8", "replace")[:2000]
            return f"Host write failed (exit {proc.returncode}): {err}"
        await self._log_op(context, f"write_file [host] ({len(content)} bytes): {target}")
        return f"Wrote {len(content)} bytes to {target} on the host."

    # ── Ops audit trail ─────────────────────────────────────────────────────────

    async def _log_op(self, context: AgentContext, line: str) -> None:
        """Always log to the structured logger; best-effort append to the ops
        trail file so the owner can review what Orion did on the host. A failure
        here never blocks the operation."""
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        entry = f"- [{stamp}] (req {context.request_id}) {line}"
        logger.info("system_ops", extra={"request_id": context.request_id, "op": line})
        try:
            db = context.db
            result = await db.execute(
                select(MemoryFile).where(
                    MemoryFile.user_id == context.user_id,
                    MemoryFile.path == OPS_LOG_PATH,
                )
            )
            f = result.scalar_one_or_none()
            if f is None:
                db.add(MemoryFile(
                    user_id=context.user_id,
                    path=OPS_LOG_PATH,
                    content=f"# Orion host-ops log\n\n{entry}\n",
                ))
            else:
                f.content = f"{f.content.rstrip()}\n{entry}\n"
                f.updated_at = datetime.now(timezone.utc)
            await db.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("system_ops_log_failed", extra={"request_id": context.request_id, "error": str(e)})
