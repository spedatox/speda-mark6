# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The academic domain — schedule storage, the attendance ledger, and the 14-week /
70% arithmetic.

Rule 1 keeps routers logic-free, so everything lives here. The attendance math
is duplicated on the watch (data/AttendanceCalculator.kt) because the watch must
compute a verdict offline; the two implementations must agree, and the rule they
both implement is stated once, here:

    scheduled  = every teaching hour the term actually holds (holidays removed)
    effective  = scheduled − hours the instructor cancelled
    allowed    = floor(effective × (1 − required_rate))
    remaining  = allowed − hours absent

The two subtractions are the part people get wrong. A cancelled class is not an
absence *and* is not a class — it leaves the denominator, so every cancellation
slightly SHRINKS the absence budget. Counter-intuitive, and correct: 70% of a
smaller number is a smaller number.

`floor` is deliberate. With 42 hours the budget is floor(12.6) = 12, not 13.
Rounding up would tell the owner he has a spare absence he does not have, on the
one question where being wrong costs a course.
"""

from __future__ import annotations

import logging
import math
from datetime import date as date_cls
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.academic import AttendanceEntry, CourseSlot, Device, TermConfig

logger = logging.getLogger(__name__)

VALID_STATUSES = {"attended", "absent", "cancelled"}

_WEEKDAY_INDEX = {
    "MONDAY": 0, "TUESDAY": 1, "WEDNESDAY": 2, "THURSDAY": 3,
    "FRIDAY": 4, "SATURDAY": 5, "SUNDAY": 6,
}


# ── Term ────────────────────────────────────────────────────────────────────

async def get_active_term(db: AsyncSession) -> TermConfig | None:
    result = await db.execute(
        select(TermConfig).where(TermConfig.active.is_(True)).order_by(TermConfig.id.desc())
    )
    return result.scalars().first()


async def upsert_term(
    db: AsyncSession,
    start_date: date_cls,
    total_weeks: int,
    required_rate: float,
    holidays: list[str],
) -> TermConfig:
    existing = await get_active_term(db)
    if existing is None:
        existing = TermConfig(active=True)
        db.add(existing)
    existing.start_date = start_date
    existing.total_weeks = total_weeks
    existing.required_rate = required_rate
    existing.holidays = ",".join(holidays)
    existing.updated_at = datetime.utcnow()
    await db.commit()
    return existing


# ── Schedule ────────────────────────────────────────────────────────────────

async def list_slots(db: AsyncSession) -> list[CourseSlot]:
    result = await db.execute(
        select(CourseSlot).where(CourseSlot.active.is_(True))
    )
    slots = list(result.scalars().all())
    # Sorted here rather than in SQL: day_of_week is a name, so ORDER BY would
    # sort it alphabetically (FRIDAY before MONDAY).
    slots.sort(key=lambda s: (_WEEKDAY_INDEX.get(s.day_of_week, 9), s.start_time))
    return slots


async def replace_schedule(db: AsyncSession, courses: list[dict]) -> int:
    """Whole-schedule replace. See ScheduleUpsertRequest for why it is not a
    per-row patch."""
    await db.execute(delete(CourseSlot))
    for c in courses:
        db.add(
            CourseSlot(
                slot_id=c["id"],
                code=c["code"],
                name=c["name"],
                instructor=c.get("instructor", ""),
                room=c.get("roomNumber", ""),
                day_of_week=c["dayOfWeek"].upper(),
                start_time=c["startTime"],
                end_time=c["endTime"],
                active=True,
            )
        )
    await db.commit()
    return len(courses)


# ── Occurrences ─────────────────────────────────────────────────────────────

def occurrences(slots: list[CourseSlot], term: TermConfig) -> list[dict]:
    """
    Expand the weekly schedule into every dated teaching hour in the term,
    skipping holidays. The denominator, the "which class just ended" lookup and
    the n8n ask-trigger all read from this.
    """
    if not slots:
        return []
    holidays = set(term.holiday_list)
    # Normalise to the Monday of the start week so week arithmetic is stable
    # even if start_date was entered as a Wednesday.
    monday = term.start_date - timedelta(days=term.start_date.weekday())

    out: list[dict] = []
    for week in range(1, term.total_weeks + 1):
        week_start = monday + timedelta(weeks=week - 1)
        for slot in slots:
            offset = _WEEKDAY_INDEX.get(slot.day_of_week)
            if offset is None:
                continue
            day = week_start + timedelta(days=offset)
            if day.isoformat() in holidays:
                continue
            out.append({
                "slot": slot,
                "date": day,
                "week": week,
                "key": f"{slot.slot_id}@{day.isoformat()}",
            })
    return out


# ── Attendance ──────────────────────────────────────────────────────────────

async def ingest_attendance(
    db: AsyncSession,
    records: list[dict],
    source: str = "wear",
) -> list[str]:
    """
    Upsert answers. Returns the keys accepted, so the watch clears its pending
    flag for exactly those.

    Last-write-wins on `recorded_at`: an older answer never overwrites a newer
    one, which is what lets the watch and the server sync in either direction
    without a coordinator.
    """
    accepted: list[str] = []
    for rec in records:
        status = str(rec.get("status", "")).lower()
        if status not in VALID_STATUSES:
            logger.warning("attendance_reject_status", extra={"status": status})
            continue

        slot_id = rec["slot_id"]
        day = rec["date"]
        if isinstance(day, str):
            day = date_cls.fromisoformat(day)
        recorded_at = int(rec.get("recorded_at") or 0)

        result = await db.execute(
            select(AttendanceEntry).where(
                AttendanceEntry.slot_id == slot_id,
                AttendanceEntry.date == day,
            )
        )
        existing = result.scalars().first()

        if existing is None:
            db.add(
                AttendanceEntry(
                    slot_id=slot_id,
                    course_code=rec.get("course_code", ""),
                    date=day,
                    status=status,
                    recorded_at=recorded_at,
                    source=source,
                )
            )
        elif recorded_at >= existing.recorded_at:
            existing.status = status
            existing.recorded_at = recorded_at
            existing.source = source
            if rec.get("course_code"):
                existing.course_code = rec["course_code"]
        else:
            # Server holds a newer answer. Not accepted, and deliberately not an
            # error — the sync response carries the newer record back instead.
            continue

        accepted.append(f"{slot_id}@{day.isoformat()}")

    await db.commit()
    return accepted


async def list_attendance(db: AsyncSession) -> list[AttendanceEntry]:
    result = await db.execute(select(AttendanceEntry).order_by(AttendanceEntry.date))
    return list(result.scalars().all())


# ── The verdict ─────────────────────────────────────────────────────────────

async def summarise(db: AsyncSession, now: datetime | None = None) -> list[dict]:
    """Per-subject attendance verdicts. The single source Ultron quotes from."""
    now = now or datetime.utcnow()
    term = await get_active_term(db)
    if term is None:
        return []
    slots = await list_slots(db)
    if not slots:
        return []

    entries = await list_attendance(db)
    by_key = {f"{e.slot_id}@{e.date.isoformat()}": e for e in entries}

    grouped: dict[str, list[dict]] = {}
    for occ in occurrences(slots, term):
        grouped.setdefault(occ["slot"].code, []).append(occ)

    summaries: list[dict] = []
    for code, occ_list in grouped.items():
        name = occ_list[0]["slot"].name
        weekly = sum(1 for s in slots if s.code == code)

        attended = absent = cancelled = unanswered = 0
        for occ in occ_list:
            entry = by_key.get(occ["key"])
            if entry is None:
                end = datetime.combine(
                    occ["date"],
                    datetime.strptime(occ["slot"].end_time, "%H:%M").time(),
                )
                if end < now:
                    unanswered += 1
                continue
            if entry.status == "attended":
                attended += 1
            elif entry.status == "absent":
                absent += 1
            elif entry.status == "cancelled":
                cancelled += 1

        scheduled = len(occ_list)
        effective = scheduled - cancelled
        allowed = math.floor(effective * (1.0 - term.required_rate))
        remaining = allowed - absent

        if remaining < 0:
            risk = "failed"
        elif remaining == 0:
            risk = "critical"
        elif remaining <= 2:
            risk = "warning"
        else:
            risk = "safe"

        summaries.append({
            "course_code": code,
            "course_name": name,
            "weekly_hours": weekly,
            "scheduled_hours": scheduled,
            "effective_hours": effective,
            "allowed_absences": allowed,
            "attended_hours": attended,
            "absent_hours": absent,
            "cancelled_hours": cancelled,
            "unanswered_hours": unanswered,
            "remaining_absences": remaining,
            "risk": risk,
        })

    summaries.sort(key=lambda s: s["remaining_absences"])
    return summaries


# ── Devices ─────────────────────────────────────────────────────────────────

async def register_device(
    db: AsyncSession, device_id: str, platform: str, fid: str
) -> Device:
    result = await db.execute(select(Device).where(Device.device_id == device_id))
    device = result.scalars().first()
    if device is None:
        device = Device(device_id=device_id)
        db.add(device)
    device.platform = platform
    device.fid = fid
    device.active = True
    device.last_seen = datetime.utcnow()
    device.updated_at = datetime.utcnow()
    await db.commit()
    return device


async def active_devices(db: AsyncSession, platform: str | None = None) -> list[Device]:
    stmt = select(Device).where(Device.active.is_(True))
    if platform:
        stmt = stmt.where(Device.platform == platform)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def deactivate_device(db: AsyncSession, fid: str) -> None:
    """Called when FCM reports an unregistered installation — the app was
    uninstalled or the data cleared. Keeping a dead fid means every future push
    burns a request and logs an error."""
    result = await db.execute(select(Device).where(Device.fid == fid))
    device = result.scalars().first()
    if device is not None:
        device.active = False
        await db.commit()


# ── The ask ─────────────────────────────────────────────────────────────────

async def occurrence_just_ended(
    db: AsyncSession, now: datetime, window_minutes: int = 20
) -> dict | None:
    """
    The teaching hour that ended within the last [window_minutes] and has no
    answer yet — what n8n's per-lecture trigger asks Igor to push about.

    Returns None when there is nothing to ask, which is the common case and must
    stay cheap: n8n fires this on a schedule, not only when a class exists.
    """
    term = await get_active_term(db)
    if term is None:
        return None
    slots = await list_slots(db)
    if not slots:
        return None

    entries = await list_attendance(db)
    answered = {f"{e.slot_id}@{e.date.isoformat()}" for e in entries}

    today = now.date()
    for occ in occurrences(slots, term):
        if occ["date"] != today:
            continue
        if occ["key"] in answered:
            continue
        end = datetime.combine(
            occ["date"],
            datetime.strptime(occ["slot"].end_time, "%H:%M").time(),
        )
        if end <= now <= end + timedelta(minutes=window_minutes):
            slot = occ["slot"]
            return {
                "slot_id": slot.slot_id,
                "course_code": slot.code,
                "course_name": slot.name,
                "date": occ["date"].isoformat(),
                "time": f"{slot.start_time} - {slot.end_time}",
                "room": slot.room,
            }
    return None
