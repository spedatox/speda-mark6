"""
The n8n-facing Outlook watch: scan the university mailbox, mark what was handled.

Thin per Rule 1 — every decision lives in services/outlook_watch.py. Authenticated
by `X-API-Key` (AuthMiddleware, Rule 12) plus the n8n shared secret, matching
`/mail/watch/*` and `/trigger`: these endpoints read the owner's mail, so the
poller has to prove it is the poller.

Neither endpoint runs a turn or touches the orchestrator. That is the point — the
LLM only wakes up when a scan actually returns something, via the workflow's own
call to `POST /trigger/{agent_id}`.
"""

import logging

from fastapi import APIRouter, Request

from app.schemas.outlook import (
    OutlookScanRequest,
    OutlookScanResponse,
    OutlookSeenRequest,
    OutlookSeenResponse,
)
from app.services import outlook_watch
from app.services.n8n import validate_n8n_secret

logger = logging.getLogger(__name__)
router = APIRouter(tags=["outlook"])


@router.post("/outlook/watch/scan", response_model=OutlookScanResponse)
async def scan_outlook(request: Request, body: OutlookScanRequest) -> dict:
    """Unseen mail from `domain` in the Microsoft mailbox, or an empty result.
    Costs zero tokens."""
    validate_n8n_secret(request)
    return await outlook_watch.scan(**body.model_dump())


@router.post("/outlook/watch/seen", response_model=OutlookSeenResponse)
async def mark_outlook_seen(request: Request, body: OutlookSeenRequest) -> dict:
    """Categorise messages as handled so the next scan skips them. Call this only
    after the trigger was accepted — an uncategorised message is retried, a
    prematurely categorised one is lost."""
    validate_n8n_secret(request)
    return await outlook_watch.mark_seen(body.message_ids, category=body.category)
