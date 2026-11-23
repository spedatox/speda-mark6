"""Owner-authenticated Operation Jarvis + ACE control surface; n8n remains the sole scheduler."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.services.n8n import validate_n8n_secret

router = APIRouter(prefix="/jarvis", tags=["jarvis"])


class GoalCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    domain: Literal["academic", "siberay", "engineering", "health", "travel", "comms", "finance", "personal"]
    description: str = Field(default="", max_length=2000)
    intention_tier: Literal["confirmed_commitment", "inferred_intention", "speculative_interest"] = "inferred_intention"
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    priority: float = Field(default=0.5, ge=0.0, le=1.0)
    deadline_at: datetime | None = None
    cue_rules: dict = Field(default_factory=dict)
    source_record_ids: list[str] = Field(default_factory=list)
    confirmed_by_owner: bool = False


class PulseRequest(BaseModel):
    external_events: list[dict] = Field(default_factory=list)
    in_calendar_event: bool = False
    active_chat_last_10m: bool = False


class ControlUpdateRequest(BaseModel):
    enabled: bool | None = None
    shadow_mode: bool | None = None
    daily_microdollars: int | None = Field(default=None, ge=0, le=5_000_000)
    daily_calls: int | None = Field(default=None, ge=0, le=200)
    daily_pushes: int | None = Field(default=None, ge=0, le=20)
    quiet_start_hour: int | None = Field(default=None, ge=0, le=23)
    quiet_end_hour: int | None = Field(default=None, ge=0, le=23)


class FeedbackRequest(BaseModel):
    initiative_id: str = Field(min_length=1, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=128)
    signal_name: str = Field(min_length=1, max_length=40)
    note: str | None = Field(default=None, max_length=500)


class ACEPolicyControlRequest(BaseModel):
    policy_scope: str = Field(min_length=1, max_length=64)
    model_head: Literal["useful", "welcome_now"]
    frozen: bool = True
    reason: str | None = Field(default=None, max_length=256)


class ACERollbackRequest(BaseModel):
    policy_scope: str = Field(min_length=1, max_length=64)
    model_head: Literal["useful", "welcome_now"]
    target_version: int = Field(ge=1)


@router.post("/pulse")
async def pulse(request: Request, body: PulseRequest | None = None):
    """n8n-scheduled heartbeat/event pulse (Phase 0 Shadow Mode: zero execution, zero push)."""
    validate_n8n_secret(request)
    from app.config import settings
    payload = body or PulseRequest()
    return await request.app.state.jarvis.run_pulse(
        1,
        timezone_name=settings.owner_timezone,
        external_events=payload.external_events,
        in_calendar_event=payload.in_calendar_event,
        active_chat_last_10m=payload.active_chat_last_10m,
    )


@router.get("/control")
async def get_control(request: Request):
    ctrl = await request.app.state.jarvis.get_control(1)
    return {
        "user_id": ctrl.user_id, "version": ctrl.version,
        "enabled": ctrl.enabled, "shadow_mode": ctrl.shadow_mode,
        "daily_microdollars": ctrl.daily_microdollars, "daily_calls": ctrl.daily_calls,
        "daily_pushes": ctrl.daily_pushes,
        "quiet_hours": [ctrl.quiet_start_hour, ctrl.quiet_end_hour],
    }


@router.put("/control")
async def update_control(request: Request, body: ControlUpdateRequest):
    try:
        return await request.app.state.jarvis.save_control(1, **body.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/goals")
async def list_goals(request: Request, status: str | None = None):
    return await request.app.state.jarvis.list_goals(1, status=status)


@router.post("/goals")
async def create_goal(request: Request, body: GoalCreateRequest):
    try:
        return await request.app.state.jarvis.create_goal(1, **body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/goals/{goal_id}/confirm")
async def confirm_goal(request: Request, goal_id: str):
    try:
        return await request.app.state.jarvis.confirm_goal(1, goal_id)
    except KeyError as exc:
        raise HTTPException(404, "Goal not found") from exc


@router.get("/initiatives")
async def list_initiatives(request: Request, status: str | None = None, limit: int = Query(50, ge=1, le=100)):
    return await request.app.state.jarvis.list_initiatives(1, status=status, limit=limit)


@router.post("/feedback")
async def submit_feedback(request: Request, body: FeedbackRequest):
    """Owner-authenticated feedback endpoint — actor_auth is pinned to 'owner_session' server-side."""
    try:
        return await request.app.state.ace.record_feedback_and_update(
            user_id=1,
            initiative_id=body.initiative_id,
            idempotency_key=body.idempotency_key,
            signal_name=body.signal_name,
            actor_auth="owner_session",
            note=body.note,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/ace/policies")
async def inspect_ace(request: Request):
    return await request.app.state.ace.inspect_policies(1)


@router.post("/ace/freeze")
async def freeze_ace(request: Request, body: ACEPolicyControlRequest):
    return await request.app.state.ace.set_frozen(
        1, body.policy_scope, body.model_head, frozen=body.frozen, reason=body.reason,
    )


@router.post("/ace/reset")
async def reset_ace(request: Request, body: ACEPolicyControlRequest):
    return await request.app.state.ace.reset_policy(1, body.policy_scope, body.model_head)


@router.post("/ace/rollback")
async def rollback_ace(request: Request, body: ACERollbackRequest):
    try:
        return await request.app.state.ace.rollback_policy(1, body.policy_scope, body.model_head, body.target_version)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
