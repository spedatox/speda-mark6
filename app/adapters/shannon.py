import logging

import httpx

from app.adapters.base import DiagnosisReport, OSSAdapter
from app.config import settings
from app.core.context import AgentContext

logger = logging.getLogger(__name__)


class ShannonAdapter(OSSAdapter):
    name = "security_analysis"
    description = (
        "Performs security analysis, penetration testing support, and threat intelligence "
        "using the Shannon security toolkit running on Contabo. "
        "Use this for vulnerability assessments, CVE analysis, network reconnaissance, "
        "or any security research task delegated by Unicron. "
        "Do not use this for general web searches or non-security tasks. "
        "Returns a structured security report as a string."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Target system, domain, or IP for analysis."},
            "analysis_type": {
                "type": "string",
                "enum": ["vuln_scan", "cve_lookup", "recon", "threat_intel"],
                "description": "Type of security analysis to perform.",
            },
        },
        "required": ["target", "analysis_type"],
    }

    def __init__(self) -> None:
        self._base_url = settings.shannon_url

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
            message=f"Shannon at {self._base_url} {'reachable' if healthy else 'unreachable'}",
        )

    async def execute(self, args: dict, context: AgentContext) -> str:
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{self._base_url}/analyze",
                    json={
                        "target": args["target"],
                        "type": args["analysis_type"],
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                return data.get("report", "No report content returned.")
        except httpx.ConnectError:
            return f"Shannon is not running at {self._base_url}."
        except Exception as e:
            logger.error("shannon_error", extra={"request_id": context.request_id, "error": str(e)})
            return f"security_analysis failed: {str(e)}"
