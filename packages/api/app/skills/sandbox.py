import logging

import httpx

from app.skills.base import Skill
from app.core.context import AgentContext
from app.config import settings

logger = logging.getLogger(__name__)


class RunCommandSkill(Skill):
    name = "run_command"
    description = (
        "Runs a shell command in SPEDA's own sandboxed Linux computer (an isolated "
        "container with Python 3.12, pip, git, curl, jq, pandas/numpy preinstalled, "
        "and a persistent /workspace). Use this to actually DO computing work: run "
        "scripts, do calculations, process data, fetch files, install packages "
        "(pip/apt as needed), generate or inspect files. Files and installed packages "
        "PERSIST across calls, so treat it like a real machine you're working on. "
        "Do NOT use it for simple math you can do in your head or for answering "
        "questions that need no execution. It cannot access the user's chat database "
        "or secrets — it is a clean, separate sandbox. Returns stdout, stderr and the "
        "exit code."
    )
    read_only = False
    input_schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to run, e.g. `python script.py` or `pip install rich && python -c \"...\"`.",
            },
            "timeout": {
                "type": "integer",
                "description": "Max seconds to allow (default 30, hard cap 120).",
                "default": 30,
            },
        },
        "required": ["command"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        if not settings.sandbox_url:
            return "The sandbox is not configured (SANDBOX_URL unset), so commands can't run."

        command = (args.get("command") or "").strip()
        if not command:
            return "No command provided."
        timeout = int(args.get("timeout", 30))

        try:
            async with httpx.AsyncClient(timeout=timeout + 10) as client:
                resp = await client.post(
                    f"{settings.sandbox_url}/exec",
                    json={"command": command, "timeout": timeout},
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:  # noqa: BLE001
            logger.error("sandbox_exec_failed", extra={"request_id": context.request_id, "error": str(e)})
            return f"Sandbox unavailable or errored: {e}"

        logger.info(
            "sandbox_exec",
            extra={"request_id": context.request_id, "exit_code": data.get("exit_code"), "timed_out": data.get("timed_out")},
        )

        # Format a compact, readable result for the model.
        out = data.get("stdout", "") or ""
        err = data.get("stderr", "") or ""
        code = data.get("exit_code", "?")
        parts = [f"exit_code: {code}" + ("  (TIMED OUT)" if data.get("timed_out") else "")]
        if out:
            parts.append(f"stdout:\n{out}")
        if err:
            parts.append(f"stderr:\n{err}")
        if not out and not err:
            parts.append("(no output)")
        return "\n\n".join(parts)


class DeliverFileSkill(Skill):
    name = "deliver_file"
    description = (
        "Delivers a file you created in the sandbox (/workspace) to the user as a "
        "downloadable file in the chat. Use this AFTER run_command has produced a "
        "file the user should receive — e.g. you generated a PDF/CSV/image with a "
        "Python script. Pass the path relative to /workspace (e.g. 'report.pdf'). "
        "The file is copied out of the sandbox and shown to the user as a download "
        "card. Returns confirmation; do not also paste a path or link."
    )
    read_only = False
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path inside /workspace, e.g. 'report.pdf'."},
            "title": {"type": "string", "description": "Friendly title for the download card."},
        },
        "required": ["path"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        import os
        from pathlib import Path
        from app.core.files import register_file

        if not settings.sandbox_url:
            return "The sandbox is not configured, so files can't be delivered from it."

        rel = (args.get("path") or "").strip()
        if not rel:
            return "No file path provided."
        title = (args.get("title") or "").strip() or Path(rel).stem

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(f"{settings.sandbox_url}/file", params={"path": rel})
                resp.raise_for_status()
                data = resp.content
        except Exception as e:  # noqa: BLE001
            logger.error("deliver_file_failed", extra={"request_id": context.request_id, "error": str(e)})
            return f"Couldn't fetch '{rel}' from the sandbox: {e}"

        out_dir = Path(settings.temp_outputs_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / os.path.basename(rel)
        dest.write_bytes(data)

        meta = register_file(context, str(dest), title=title)
        logger.info("deliver_file", extra={"request_id": context.request_id, "file_name": meta["name"], "size": meta["size"]})
        return (
            f"Delivered '{meta['title']}' ({meta['size']} bytes) to the user as a "
            f"downloadable file. Just tell them it's ready — no path or link needed."
        )
