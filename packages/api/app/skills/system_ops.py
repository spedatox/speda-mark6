"""
system_ops — Orion's host operation skill (docs/MEMORY_ARCHITECTURE.md §3.5).

This is the ONE privileged skill that touches the actual host Mark VI runs on,
as opposed to run_command's isolated sandbox container. It exists so the memory
custodian can do real maintenance — rotate logs, inspect /tmp/speda_outputs, run
disk/health checks, look at why a container is eating RAM — when the owner asks.

Hard scoping (defence in depth):
  - restricted_to = {"orion"}: the registry hides it from every other agent and
    refuses to execute it for anyone else, regardless of allowlist.
  - settings.system_ops_enabled gates it OFF by default — a deployment must opt in.
  - a deny-list blocks obviously catastrophic commands.
  - file writes are jailed under settings.system_ops_root.
  - every invocation is logged (logger + /memories/.audit/ops.md) with request_id.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

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


class SystemOpsSkill(Skill):
    name = "system_ops"
    restricted_to = frozenset({"orion"})
    read_only = False
    requires_network = False
    description = (
        "Operate the HOST computer Mark VI runs on — Orion's maintenance hands. "
        "Use it for real system upkeep: inspect disk/memory/processes, rotate or read "
        "logs, look inside /tmp/speda_outputs, check why a service or container is "
        "misbehaving, and read or write maintenance files under the ops root. Do NOT use "
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
                "enum": ["exec", "read_file", "write_file"],
                "description": "exec: run a shell command. read_file: read a host file. write_file: write a file under the ops root.",
            },
            "command": {"type": "string", "description": "Shell command for action=exec."},
            "path": {"type": "string", "description": "Absolute host path for read_file/write_file."},
            "content": {"type": "string", "description": "File content for write_file."},
            "timeout": {
                "type": "integer",
                "description": f"Max seconds for exec (default 30, hard cap {settings.system_ops_timeout}).",
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
        # Belt-and-braces: the registry already blocks non-Orion callers, but never
        # trust a single gate for a host-privileged tool.
        if context.agent_id != "orion":
            return "system_ops is restricted to Orion."

        action = (args.get("action") or "").strip()
        if action == "exec":
            return await self._exec(args, context)
        if action == "read_file":
            return await self._read_file(args, context)
        if action == "write_file":
            return await self._write_file(args, context)
        return f"Error: unknown action '{action}'. Valid: exec, read_file, write_file."

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
        await self._log_op(context, f"exec (exit={code}): {command}")

        parts = [f"exit_code: {code}"]
        if out:
            parts.append(f"stdout:\n{out[:8000]}")
        if err:
            parts.append(f"stderr:\n{err[:4000]}")
        if not out and not err:
            parts.append("(no output)")
        return "\n\n".join(parts)

    async def _read_file(self, args: dict, context: AgentContext) -> str:
        raw = (args.get("path") or "").strip()
        if not raw:
            return "No path provided."
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
