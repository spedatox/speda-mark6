"""
Persistent reminders: the n8n tick, plus owner-facing status.

Thin per Rule 1 — every decision lives in services/reminders.py. The tick
carries both secrets like the other probes (X-API-Key via AuthMiddleware plus
X-N8N-Secret here); the read-only status endpoints need only the API key, since
they are for the owner's UI rather than the poller.

No endpoint here runs a turn. Asking, re-asking and recording an answer are all
deterministic — that is what lets a reminder nag every 5 minutes without costing
anything.
"""

import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.reminder import (
    ReminderAnswerRequest,
    ReminderTickRequest,
    ReminderTickResponse,
)
from app.services import reminders as reminder_service
from app.services.n8n import validate_n8n_secret

logger = logging.getLogger(__name__)
router = APIRouter(tags=["reminders"])


@router.post("/reminders/tick", response_model=ReminderTickResponse)
async def tick(
    request: Request,
    body: ReminderTickRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Open due reminders, re-ask open ones, give up on exhausted ones."""
    validate_n8n_secret(request)
    return await reminder_service.tick(
        db,
        agent_id=body.agent,
        reminders=[r.model_dump() for r in body.reminders],
        bots=request.app.state.telegram_bots,
    )


@router.get("/reminders/open")
async def open_reminders(agent: str = "", db: AsyncSession = Depends(get_db)) -> dict:
    """Questions currently waiting on an answer."""
    return {"open": await reminder_service.list_open(db, agent_id=agent)}


@router.get("/reminders/history")
async def reminder_history(
    reminder_id: str = "", limit: int = 30, db: AsyncSession = Depends(get_db)
) -> dict:
    """Closed cycles, newest first — answered, gave_up or cancelled."""
    return {"history": await reminder_service.history(db, reminder_id=reminder_id, limit=limit)}


@router.post("/reminders/answer")
async def answer(
    request: Request, body: ReminderAnswerRequest, db: AsyncSession = Depends(get_db)
) -> dict:
    """Close a reminder by hand (desktop UI / testing). The everyday paths are a
    button tap and the agent's `reminders` tool."""
    bots = request.app.state.telegram_bots
    if body.cycle_id:
        return await reminder_service.answer(db, body.cycle_id, body.answer, via="chat", bots=bots)
    return await reminder_service.answer_latest(
        db, body.answer, reminder_id=body.reminder_id, bots=bots
    )
