"""
n8n REST API client — SPEDA's control plane over the automation engine.

n8n owns all scheduling and polling (CLAUDE.md: "the sole scheduling and
automation organ"). SPEDA never schedules internally; it composes workflow JSON
(see app/automations/composer.py) and uses this client to create / list /
activate / delete those workflows and read their execution history.
"""

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class N8nClient:
    """Async wrapper over the n8n public REST API (`/api/v1`). Injected on
    app.state at startup; degrades gracefully (logs, returns empty/None) when
    n8n is unreachable or no API key is set."""

    def __init__(self) -> None:
        self._base = settings.n8n_api_url.rstrip("/") + "/api/v1"
        self._key = settings.n8n_api_key
        # Last upstream failure detail (status + response body) so callers can
        # surface n8n's actual validation message instead of a blind "failed".
        self.last_error: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self._key)

    def _headers(self) -> dict:
        return {"X-N8N-API-KEY": self._key, "Accept": "application/json"}

    async def _request(self, method: str, path: str, **kwargs):
        if not self.configured:
            logger.warning("n8n_not_configured", extra={"path": path})
            return None
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.request(
                    method, f"{self._base}{path}", headers=self._headers(), **kwargs
                )
                resp.raise_for_status()
                self.last_error = None
                return resp.json() if resp.content else {}
        except httpx.HTTPStatusError as exc:
            # n8n puts the real reason in the response body (e.g. schema
            # validation: "request/body/nodes/0 must have required property 'id'").
            body = (exc.response.text or "")[:600]
            self.last_error = f"HTTP {exc.response.status_code}: {body}"
            logger.error(
                "n8n_request_failed",
                extra={"method": method, "path": path,
                       "status": exc.response.status_code, "body": body},
            )
            return None
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            logger.error("n8n_request_failed", extra={"method": method, "path": path, "error": str(exc)})
            return None

    async def ping(self) -> bool:
        """True if the n8n API answers — used by health checks and the UI."""
        return await self._request("GET", "/workflows?limit=1") is not None

    async def list_workflows(self) -> list[dict]:
        data = await self._request("GET", "/workflows")
        return (data or {}).get("data", []) if isinstance(data, dict) else (data or [])

    async def get_workflow(self, workflow_id: str) -> dict | None:
        return await self._request("GET", f"/workflows/{workflow_id}")

    async def create_workflow(self, workflow: dict) -> dict | None:
        """POST a composed workflow. Returns the created object (with `id`) or
        None on failure — the caller surfaces the error for repair."""
        return await self._request("POST", "/workflows", json=workflow)

    async def update_workflow(self, workflow_id: str, workflow: dict) -> dict | None:
        return await self._request("PUT", f"/workflows/{workflow_id}", json=workflow)

    async def set_active(self, workflow_id: str, active: bool) -> bool:
        verb = "activate" if active else "deactivate"
        return await self._request("POST", f"/workflows/{workflow_id}/{verb}") is not None

    async def delete_workflow(self, workflow_id: str) -> bool:
        return await self._request("DELETE", f"/workflows/{workflow_id}") is not None

    async def get_executions(self, workflow_id: str, limit: int = 5) -> list[dict]:
        data = await self._request(
            "GET", f"/executions?workflowId={workflow_id}&limit={limit}"
        )
        return (data or {}).get("data", []) if isinstance(data, dict) else (data or [])
