"""
The n8n-facing Lifeboat watch: read the host's headroom, ack what was reported.

Thin per Rule 1 — every threshold, every edge decision and every reclamation
lives in services/lifeboat.py. Same auth posture as /web/watch/* and /trigger:
X-API-Key (AuthMiddleware, Rule 12) plus the n8n shared secret, because these
read the host's disk and memory and the poller should have to prove it is the
poller.

Neither n8n-facing endpoint runs a turn or reclaims a byte. The LLM only wakes
up when a scan reports `changed: true`, via the workflow's own call to
POST /trigger/orion.
"""

import logging

from fastapi import APIRouter, Request

from app.schemas.lifeboat import (
    LifeboatAckRequest,
    LifeboatAckResponse,
    LifeboatScanResponse,
    LifeboatState,
)
from app.services import lifeboat
from app.services.n8n import validate_n8n_secret

logger = logging.getLogger(__name__)
router = APIRouter(tags=["lifeboat"])


@router.post("/host/lifeboat/scan", response_model=LifeboatScanResponse)
async def scan_host(request: Request) -> dict:
    """Has the host crossed a resource line since the owner was last told?

    One SSH round trip, no model, no tokens. Returns `changed: false` on the
    overwhelming majority of polls, which is what stops the branch."""
    validate_n8n_secret(request)
    return await lifeboat.scan()


@router.post("/host/lifeboat/ack", response_model=LifeboatAckResponse)
async def ack_level(request: Request, body: LifeboatAckRequest) -> dict:
    """Commit the scanned level as reported. Call this only AFTER the trigger was
    accepted — an unacked escalation is reported again next poll, an acked one is
    never reported again until the level moves."""
    validate_n8n_secret(request)
    return lifeboat.ack(body.level)


# ── Owner-facing (X-API-Key only; these are for the human, not the poller) ────

@router.get("/host/lifeboat", response_model=LifeboatState)
async def lifeboat_assessment() -> dict:
    """What the host looks like right now and what the protocol would propose.

    Reclaims nothing — this is the assessment the owner reads before deciding.
    Use it when a push arrived and you want the current numbers rather than the
    ones from the poll that fired it."""
    return await lifeboat.assess()


@router.delete("/host/lifeboat")
async def reset_watch() -> dict:
    """Forget what the owner has been told, so the next crossing reports again.
    The fix after changing thresholds, or when a push was missed."""
    return lifeboat.reset()
