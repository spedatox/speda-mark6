import logging
import os

from app.skills.base import Skill
from app.core.context import AgentContext

logger = logging.getLogger(__name__)


class SystemSkill(Skill):
    name = "system_info"
    description = (
        "Returns basic system information about the Contabo server SPEDA runs on, "
        "such as disk usage, memory usage, and uptime. "
        "Use this when the user asks about server health, available disk space, or system status. "
        "Do not use this for application-level metrics — use the /health endpoint for those. "
        "Returns a plain text summary of system metrics."
    )
    read_only = True
    input_schema = {
        "type": "object",
        "properties": {
            "metric": {
                "type": "string",
                "enum": ["disk", "memory", "uptime", "all"],
                "description": "Which metric to report. Use 'all' for a full summary.",
                "default": "all",
            }
        },
        "required": [],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        metric = args.get("metric", "all")
        lines = []

        if metric in ("disk", "all"):
            statvfs = os.statvfs("/")
            total_gb = (statvfs.f_blocks * statvfs.f_frsize) / (1024 ** 3)
            free_gb = (statvfs.f_bfree * statvfs.f_frsize) / (1024 ** 3)
            lines.append(f"Disk: {free_gb:.1f} GB free / {total_gb:.1f} GB total")

        if metric in ("memory", "all"):
            try:
                with open("/proc/meminfo") as f:
                    meminfo = {
                        line.split(":")[0]: line.split(":")[1].strip()
                        for line in f.readlines()
                        if ":" in line
                    }
                lines.append(f"Memory total: {meminfo.get('MemTotal', 'N/A')}")
                lines.append(f"Memory available: {meminfo.get('MemAvailable', 'N/A')}")
            except Exception:
                lines.append("Memory info unavailable")

        if metric in ("uptime", "all"):
            try:
                with open("/proc/uptime") as f:
                    uptime_seconds = float(f.read().split()[0])
                    hours = int(uptime_seconds // 3600)
                    minutes = int((uptime_seconds % 3600) // 60)
                    lines.append(f"Uptime: {hours}h {minutes}m")
            except Exception:
                lines.append("Uptime info unavailable")

        return "\n".join(lines) if lines else "No metrics collected."
