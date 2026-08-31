# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Attendance ledger tests (docs/ULTRON_WEAR.md).

The arithmetic here decides whether the owner is told he can miss a class. Being
wrong in the generous direction fails a course, so the cases below pin down the
three things that are easy to get wrong:

  1. the denominator (holidays removed, cancellations removed)
  2. `floor`, not `round`, on the absence budget
  3. cancelled ≠ absent

Plus sync idempotency and last-write-wins, because the watch re-sends records
whose POST failed and must not double-count them.

Runs against a real in-memory SQLite so the unique constraint and the upsert
path are genuinely exercised, not mocked.
"""

from datetime import date, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.models.academic import AttendanceEntry, CourseSlot
from app.services import academic as ac

# 2026-09-21 is a Monday — week 1 of the term.
TERM_START = date(2026, 9, 21)


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as session:
        yield session
    await engine.dispose()


async def _seed(db, *, weeks=14, rate=0.70, holidays=None, hours=3):
    """A single 3-hour Monday course, so the term is 14 × 3 = 42 hours."""
    await ac.upsert_term(db, TERM_START, weeks, rate, holidays or [])
    courses = [
        {
            "id": f"phys101_mon_{9 + i:02d}00",
            "code": "PHYS101",
            "name": "Fizik I",
            "instructor": "Dr. R. Wilson",
            "roomNumber": "C-310",
            "dayOfWeek": "MONDAY",
            "startTime": f"{9 + i:02d}:00",
            "endTime": f"{9 + i:02d}:50",
        }
        for i in range(hours)
    ]
    await ac.replace_schedule(db, courses)
    return courses


async def _answer(db, slot_id, day, status, recorded_at=1):
    await ac.ingest_attendance(
        db,
        [{
            "slot_id": slot_id,
            "course_code": "PHYS101",
            "date": day,
            "status": status,
            "recorded_at": recorded_at,
        }],
    )


# ── The denominator ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scheduled_hours_is_weeks_times_weekly_hours(db):
    await _seed(db)
    summaries = await ac.summarise(db, now=datetime(2026, 9, 21, 8, 0))
    assert len(summaries) == 1
    s = summaries[0]
    assert s["weekly_hours"] == 3
    assert s["scheduled_hours"] == 42          # 14 weeks × 3 hours
    assert s["effective_hours"] == 42          # nothing cancelled yet


@pytest.mark.asyncio
async def test_holiday_removes_occurrences_from_the_denominator(db):
    # 2026-10-19 is a Monday in week 5 — a holiday kills all 3 of its hours.
    await _seed(db, holidays=["2026-10-19"])
    s = (await ac.summarise(db, now=datetime(2026, 9, 21, 8, 0)))[0]
    assert s["scheduled_hours"] == 39          # 42 − 3
    # floor(39 × 0.30) = floor(11.7) = 11
    assert s["allowed_absences"] == 11


# ── floor, not round ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_budget_floors_rather_than_rounding(db):
    """42 × 0.30 = 12.6. The budget is 12, not 13.

    Rounding up here would hand the owner a spare absence he does not have, on
    the one question where being wrong costs a course."""
    await _seed(db)
    s = (await ac.summarise(db, now=datetime(2026, 9, 21, 8, 0)))[0]
    assert s["allowed_absences"] == 12
    assert s["remaining_absences"] == 12


# ── cancelled ≠ absent ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cancelled_leaves_the_denominator_and_is_not_an_absence(db):
    courses = await _seed(db)
    slot = courses[0]["id"]
    await _answer(db, slot, date(2026, 9, 21), "cancelled")

    s = (await ac.summarise(db, now=datetime(2026, 9, 28, 8, 0)))[0]
    assert s["cancelled_hours"] == 1
    assert s["absent_hours"] == 0
    assert s["effective_hours"] == 41          # 42 − 1 cancelled
    # floor(41 × 0.30) = floor(12.3) = 12 — the budget did NOT grow.
    assert s["allowed_absences"] == 12
    assert s["remaining_absences"] == 12


@pytest.mark.asyncio
async def test_enough_cancellations_shrink_the_budget(db):
    """This is the counter-intuitive half of the rule: 70% of a smaller number
    is a smaller number, so cancellations can COST you an absence."""
    courses = await _seed(db)
    # Cancel 5 hours across the term.
    for i, day in enumerate([date(2026, 9, 21), date(2026, 9, 28)]):
        for slot_index in range(3 if i == 0 else 2):
            await _answer(db, courses[slot_index]["id"], day, "cancelled")

    s = (await ac.summarise(db, now=datetime(2026, 10, 5, 8, 0)))[0]
    assert s["cancelled_hours"] == 5
    assert s["effective_hours"] == 37
    # floor(37 × 0.30) = floor(11.1) = 11 — one fewer than the 12 we started with.
    assert s["allowed_absences"] == 11


@pytest.mark.asyncio
async def test_absence_consumes_budget(db):
    courses = await _seed(db)
    await _answer(db, courses[0]["id"], date(2026, 9, 21), "absent")
    await _answer(db, courses[1]["id"], date(2026, 9, 21), "absent")

    s = (await ac.summarise(db, now=datetime(2026, 9, 28, 8, 0)))[0]
    assert s["absent_hours"] == 2
    assert s["remaining_absences"] == 10
    assert s["risk"] == "safe"


# ── Risk thresholds ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_risk_escalates_as_the_budget_empties(db):
    courses = await _seed(db)
    now = datetime(2027, 1, 4, 8, 0)   # after the term, so nothing is pending

    async def burn(n):
        # Spread absences across distinct occurrences.
        day = date(2026, 9, 21)
        used = 0
        while used < n:
            for c in courses:
                if used >= n:
                    break
                await _answer(db, c["id"], day, "absent")
                used += 1
            day = date.fromordinal(day.toordinal() + 7)

    await burn(10)
    assert (await ac.summarise(db, now=now))[0]["risk"] == "warning"   # 2 left

    await burn(12)
    s = (await ac.summarise(db, now=now))[0]
    assert s["remaining_absences"] == 0
    assert s["risk"] == "critical"


# ── Sync semantics ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reingesting_the_same_occurrence_does_not_double_count(db):
    """The watch re-sends records whose POST failed. They must collapse."""
    courses = await _seed(db)
    slot = courses[0]["id"]
    payload = [{
        "slot_id": slot,
        "course_code": "PHYS101",
        "date": date(2026, 9, 21),
        "status": "absent",
        "recorded_at": 1000,
    }]
    await ac.ingest_attendance(db, payload)
    await ac.ingest_attendance(db, payload)

    entries = await ac.list_attendance(db)
    assert len(entries) == 1
    assert (await ac.summarise(db, now=datetime(2026, 9, 28)))[0]["absent_hours"] == 1


@pytest.mark.asyncio
async def test_newer_answer_wins_and_older_is_ignored(db):
    courses = await _seed(db)
    slot = courses[0]["id"]
    day = date(2026, 9, 21)

    await _answer(db, slot, day, "absent", recorded_at=2000)
    # A correction made later on the watch.
    await _answer(db, slot, day, "attended", recorded_at=3000)
    s = (await ac.summarise(db, now=datetime(2026, 9, 28)))[0]
    assert s["attended_hours"] == 1 and s["absent_hours"] == 0

    # A stale record arriving late must NOT clobber the newer answer.
    await _answer(db, slot, day, "absent", recorded_at=1000)
    s = (await ac.summarise(db, now=datetime(2026, 9, 28)))[0]
    assert s["attended_hours"] == 1 and s["absent_hours"] == 0


@pytest.mark.asyncio
async def test_invalid_status_is_rejected_not_stored(db):
    courses = await _seed(db)
    accepted = await ac.ingest_attendance(
        db,
        [{
            "slot_id": courses[0]["id"],
            "course_code": "PHYS101",
            "date": date(2026, 9, 21),
            "status": "maybe",
            "recorded_at": 1,
        }],
    )
    assert accepted == []
    assert await ac.list_attendance(db) == []


# ── Unanswered tracking ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_only_elapsed_hours_count_as_unanswered(db):
    await _seed(db)
    # Mid-morning on day one: the 09:00 is over, the 10:00 and 11:00 are not.
    s = (await ac.summarise(db, now=datetime(2026, 9, 21, 10, 30)))[0]
    assert s["unanswered_hours"] == 1


# ── The ask trigger ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_occurrence_just_ended_finds_the_lecture_in_window(db):
    await _seed(db)
    # 09:50 lecture ended; 09:55 is inside the 20-minute window.
    occ = await ac.occurrence_just_ended(db, datetime(2026, 9, 21, 9, 55))
    assert occ is not None
    assert occ["slot_id"] == "phys101_mon_0900"
    assert occ["time"] == "09:00 - 09:50"


@pytest.mark.asyncio
async def test_occurrence_just_ended_skips_already_answered(db):
    courses = await _seed(db)
    await _answer(db, courses[0]["id"], date(2026, 9, 21), "attended")
    occ = await ac.occurrence_just_ended(db, datetime(2026, 9, 21, 9, 55))
    assert occ is None


@pytest.mark.asyncio
async def test_occurrence_just_ended_returns_none_outside_window(db):
    await _seed(db)
    # An hour later the question is stale; the watch's local fallback owns it.
    assert await ac.occurrence_just_ended(db, datetime(2026, 9, 21, 11, 30)) is None
