"""
The n8n-facing mail watch: scan for mail from a domain, mark what was handled.

Thin per Rule 1 — every decision lives in services/mail_watch.py. Authenticated
by `X-API-Key` (AuthMiddleware, Rule 12) plus the n8n shared secret, matching the
posture of `/trigger` and `/academic/ask-pending`: these endpoints read the
owner's mail, so the poller has to prove it is the poller.

Neither endpoint runs a turn or touches the orchestrator. That is the point —
the LLM only wakes up when a scan actually returns something, via the workflow's
own call to `POST /trigger/{agent_id}`.
"""

import logging

from fastapi import APIRouter, Request

from app.schemas.mail import (
    MailScanRequest,
    MailScanResponse,
    MailSeenRequest,
    MailSeenResponse,
)
from app.services import mail_watch
from app.services.n8n import validate_n8n_secret

logger = logging.getLogger(__name__)
router = APIRouter(tags=["mail"])


@router.post("/mail/watch/scan", response_model=MailScanResponse)
async def scan_mail(request: Request, body: MailScanRequest) -> dict:
    """Unseen mail from `domain`, or an empty result. Costs zero tokens."""
    validate_n8n_secret(request)
    return await mail_watch.scan(**body.model_dump())


@router.post("/mail/watch/seen", response_model=MailSeenResponse)
async def mark_mail_seen(request: Request, body: MailSeenRequest) -> dict:
    """Label messages as handled so the next scan skips them. Call this only
    after the trigger was accepted — an unlabelled message is retried, a
    prematurely labelled one is lost."""
    validate_n8n_secret(request)
    return await mail_watch.mark_seen(body.message_ids, label=body.label)
