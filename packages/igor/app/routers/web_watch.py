# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The n8n-facing web watch: scan a page for what got published, ack what was sent.

Thin per Rule 1 — every decision lives in services/web_watch.py. Same auth
posture as /mail/watch/* and /trigger: X-API-Key (AuthMiddleware, Rule 12) plus
the n8n shared secret.

Neither endpoint runs a turn. The LLM only wakes up when a scan reports
`changed: true`, via the workflow's own call to POST /trigger/{agent_id}.
"""

import logging

from fastapi import APIRouter, Request

from app.schemas.web import (
    WebAckRequest,
    WebAckResponse,
    WebScanRequest,
    WebScanResponse,
)
from app.services import web_watch
from app.services.n8n import validate_n8n_secret

logger = logging.getLogger(__name__)
router = APIRouter(tags=["web-watch"])


@router.post("/web/watch/scan", response_model=WebScanResponse)
async def scan_page(request: Request, body: WebScanRequest) -> dict:
    """What appeared on this page since the last ack. Costs zero tokens."""
    validate_n8n_secret(request)
    return await web_watch.scan(**body.model_dump())


@router.post("/web/watch/ack", response_model=WebAckResponse)
async def ack_page(request: Request, body: WebAckRequest) -> dict:
    """Commit the scanned snapshot as the new baseline. Call this only after the
    trigger was accepted — an unacked scan is reported again next poll, an
    ack'd one is never reported at all."""
    validate_n8n_secret(request)
    return web_watch.ack(body.watch_id, body.fingerprint)


# ── Owner-facing (X-API-Key only; these are for the human, not the poller) ────

@router.get("/web/watch")
async def list_watches() -> dict:
    """Every page Igor holds a snapshot for. Use it to check a watch_id in the
    n8n list matches one here — a typo there silently creates a second watch
    that baselines instead of reporting."""
    return {"watches": web_watch.list_watches()}


@router.delete("/web/watch/{watch_id}")
async def reset_watch(watch_id: str) -> dict:
    """Forget this watch's snapshot; the next scan re-baselines it silently.
    The fix after changing `ignore` or when a redesign made the diff useless."""
    return web_watch.reset(watch_id)
