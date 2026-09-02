# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The Ultron Wear surface: schedule out, attendance in, device registration.

Thin per Rule 1 — every decision lives in services/academic.py. Authenticated
by `X-API-Key` via AuthMiddleware like every other endpoint (Rule 12); note that
nothing here is exempt, unlike the `/health` liveness probe.
"""

import logging
import time
from datetime import date as date_cls
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import owner_now
from app.database import get_db
from app.schemas.academic import (
    AttendanceRecordOut,
    AttendanceSyncRequest,
    AttendanceSyncResponse,
    CourseOut,
    DeviceRegisterRequest,
    ScheduleOut,
    ScheduleUpsertRequest,
    TermOut,
)
from app.services import academic as academic_service
from app.services import fcm
from app.services.n8n import validate_n8n_secret

logger = logging.getLogger(__name__)
router = APIRouter(tags=["academic"])


@router.get("/academic/schedule", response_model=ScheduleOut)
async def get_schedule(db: AsyncSession = Depends(get_db)) -> ScheduleOut:
    """The timetable the watch caches and renders offline."""
    term = await academic_service.get_active_term(db)
    slots = await academic_service.list_slots(db)
    return ScheduleOut(
        term=TermOut(
            start_date=term.start_date.isoformat(),
            total_weeks=term.total_weeks,
            required_rate=term.required_rate,
            holidays=term.holiday_list,
        ) if term else None,
        courses=[
            CourseOut(
                id=s.slot_id,
                code=s.code,
                name=s.name,
                instructor=s.instructor,
                roomNumber=s.room,
                dayOfWeek=s.day_of_week,
                startTime=s.start_time,
                endTime=s.end_time,
            )
            for s in slots
        ],
        updated_at=int(time.time()),
    )


@router.put("/academic/schedule")
async def put_schedule(
    body: ScheduleUpsertRequest, db: AsyncSession = Depends(get_db)
) -> dict:
    """Replace the whole timetable and term configuration, then push every
    registered watch to resync now rather than on its own six-hour heartbeat."""
    await academic_service.upsert_term(
        db,
        start_date=date_cls.fromisoformat(body.term.start_date),
        total_weeks=body.term.total_weeks,
        required_rate=body.term.required_rate,
        holidays=body.term.holidays,
    )
    count = await academic_service.replace_schedule(
        db, [c.model_dump() for c in body.courses]
    )

    # Same device loop as ask-pending, below — just a different payload type.
    devices = await academic_service.active_devices(db, platform="wear")
    delivered = 0
    for device in devices:
        ok, detail = await fcm.send_data_message(
            fid=device.fid, data={"type": "sync_request"}, priority="high",
            token=device.token,
        )
        if ok:
            delivered += 1
        elif detail == "unregistered":
            await academic_service.deactivate_device(db, device.fid)

    return {"status": "ok", "courses": count, "devices_notified": delivered}


@router.post("/academic/attendance", response_model=AttendanceSyncResponse)
async def sync_attendance(
    body: AttendanceSyncRequest, db: AsyncSession = Depends(get_db)
) -> AttendanceSyncResponse:
    """
    Bidirectional settle: accept the watch's answers, hand back anything the
    server holds. One round trip, so a brief window of connectivity resolves
    both directions.
    """
    accepted = await academic_service.ingest_attendance(
        db, [r.model_dump() for r in body.records], source=body.device or "wear"
    )
    entries = await academic_service.list_attendance(db)
    return AttendanceSyncResponse(
        accepted=accepted,
        records=[
            AttendanceRecordOut(
                slot_id=e.slot_id,
                course_code=e.course_code,
                date=e.date.isoformat(),
                status=e.status,
                recorded_at=e.recorded_at,
            )
            for e in entries
        ],
    )


@router.get("/academic/attendance/summary")
async def attendance_summary(db: AsyncSession = Depends(get_db)) -> dict:
    """Per-subject verdicts — the same numbers the watch shows and Ultron
    quotes."""
    return {"summaries": await academic_service.summarise(db)}


@router.post("/academic/ask-pending")
async def ask_pending(
    request: Request,
    window_minutes: int = 20,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Push the "derse girdin mi?" question for a lecture that just ended.

    ── Why this is a plain endpoint and not a /trigger/ultron turn ──────────
    n8n remains the sole scheduling organ — it owns the cron, this just performs
    the action, exactly like the `DELETE /admin/outputs` cleanup n8n already
    calls on a schedule. Routing it through `POST /trigger/ultron` instead would
    spend a full agentic turn every five minutes to discover that no class ended,
    roughly a hundred LLM calls a day to answer "no". There is no reasoning in
    this decision: a lecture either just ended unanswered or it did not.

    Authenticated by X-API-Key (middleware) plus the n8n shared secret, matching
    the trigger endpoint's posture.
    """
    validate_n8n_secret(request)

    # owner_now(), not datetime.now(): the timetable stores wall-clock "HH:MM"
    # in the owner's zone, and the container runs on UTC — comparing the two
    # directly asked about the wrong lecture, three hours out (core/clock.py).
    occurrence = await academic_service.occurrence_just_ended(
        db, owner_now(), window_minutes=window_minutes
    )
    if occurrence is None:
        return {"status": "idle", "asked": None}

    devices = await academic_service.active_devices(db, platform="wear")
    if not devices:
        logger.info("attendance_ask_no_devices", extra={"slot_id": occurrence["slot_id"]})
        return {"status": "no_devices", "asked": occurrence["slot_id"]}

    delivered, failures = 0, []
    for device in devices:
        ok, detail = await fcm.send_attendance_ask(device.fid, occurrence, device.token)
        if ok:
            delivered += 1
        elif detail == "unregistered":
            await academic_service.deactivate_device(db, device.fid)
            failures.append(f"{device.device_id}: unregistered")
        else:
            failures.append(f"{device.device_id}: {detail}")

    logger.info(
        "attendance_ask_pushed",
        extra={"slot_id": occurrence["slot_id"], "delivered": delivered},
    )
    return {
        "status": "sent" if delivered else "failed",
        "asked": occurrence["slot_id"],
        "course": occurrence["course_name"],
        "delivered": delivered,
        "failures": failures,
    }


@router.post("/devices/register")
async def register_device(
    body: DeviceRegisterRequest, db: AsyncSession = Depends(get_db)
) -> dict:
    """Store the device's Firebase Installation ID so Igor can push to it."""
    await academic_service.register_device(
        db, body.device, body.platform, body.fid, body.token
    )
    return {"status": "ok"}
