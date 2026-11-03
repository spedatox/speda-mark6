import logging

import httpx

from app.adapters.base import DiagnosisReport, OSSAdapter, RecoveryResult
from app.config import settings
from app.core.context import AgentContext

logger = logging.getLogger(__name__)


class GptResearcherAdapter(OSSAdapter):
    name = "deep_research"
    description = (
        "Performs deep, multi-source web research on a topic using the gpt-researcher OSS engine. "
        "Use this when a task requires comprehensive research across many sources that would "
        "take too many individual tool calls in the main loop — briefings, technical deep dives, "
        "market analysis, academic topic overviews. "
        "Do not use this for simple lookups or queries answerable in 1–2 searches. "
        "Returns a detailed research report as a string with citations."
    )
    read_only = True
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The research question or topic."},
            "report_type": {
                "type": "string",
                "enum": ["research_report", "outline_report", "resource_report"],
                "default": "research_report",
            },
        },
        "required": ["query"],
    }

    def __init__(self) -> None:
        self._base_url = settings.gpt_researcher_url

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self._base_url}/health")
                return resp.status_code == 200
        except Exception:
            return False

    async def diagnose(self) -> DiagnosisReport:
        healthy = await self.health_check()
        return DiagnosisReport(
            healthy=healthy,
            message=f"gpt-researcher at {self._base_url} {'reachable' if healthy else 'unreachable'}",
        )

    async def execute(self, args: dict, context: AgentContext) -> str:
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    f"{self._base_url}/report",
                    json={
                        "query": args["query"],
                        "report_type": args.get("report_type", "research_report"),
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                return data.get("report", "No report content returned.")
        except httpx.ConnectError:
            return f"gpt-researcher is not running at {self._base_url}. Start it before using deep_research."
        except Exception as e:
            logger.error("gpt_researcher_error", extra={"request_id": context.request_id, "error": str(e)})
            return f"deep_research failed: {str(e)}"
