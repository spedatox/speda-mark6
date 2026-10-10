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

from contextlib import asynccontextmanager
from datetime import date, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.context import AgentContext
from app.database import Base
from app.models.academic import AttendanceEntry, CourseSlot
from app.services import academic as ac
from app.skills import attendance as attendance_skills

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


# ── Recovery of unanswered hours ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pending_and_summary_use_owner_clock_at_the_bell(db, monkeypatch):
    await _seed(db)
    # The schedule is owner-local. At UTC+03, 09:50 UTC would be three hours late.
    monkeypatch.setattr(ac, "owner_now", lambda: datetime(2026, 9, 21, 9, 50))

    summary = (await ac.summarise(db))[0]
    pending = await ac.pending_occurrences(db)

    assert summary["unanswered_hours"] == 1
    assert [p["slot_id"] for p in pending] == ["phys101_mon_0900"]


@pytest.mark.asyncio
async def test_recovery_keeps_only_missing_elapsed_teaching_hours(db):
    courses = await _seed(db, weeks=3, hours=4, holidays=["2026-09-28"])
    # A partially answered course: every settled status must stay settled.
    for index, status in [(0, "attended"), (1, "absent"), (3, "cancelled")]:
        await _answer(db, courses[index]["id"], TERM_START, status)

    now = datetime(2026, 9, 29, 12, 0)
    pending = await ac.pending_occurrences(db, now)

    # Week 2 is a holiday, week 3 is still future, and only one week-1 hour is missing.
    assert [(p["slot_id"], p["date"], p["time"]) for p in pending] == [
        (courses[2]["id"], "2026-09-21", "11:00 - 11:50")
    ]
    assert await ac.occurrence_just_ended(db, now) is None


@pytest.mark.asyncio
async def test_pending_occurrences_can_target_one_hour_and_date(db):
    courses = await _seed(db, weeks=2, hours=2)
    now = datetime(2026, 9, 29, 12, 0)

    pending = await ac.pending_occurrences(
        db, now, course_code="PHYS101", on_date=date(2026, 9, 28),
        slot_id=courses[1]["id"],
    )
    assert [(p["slot_id"], p["date"]) for p in pending] == [
        (courses[1]["id"], "2026-09-28")
    ]
    assert await ac.pending_occurrences(db, now, course_code="OTHER101") == []
    assert await ac.pending_occurrences(db, now, slot_id="missing-slot") == []
    # Matching weekday outside the term is not an attendance occurrence.
    assert await ac.pending_occurrences(db, now, on_date=date(2026, 9, 14)) == []
    assert await ac.pending_occurrences(db, now, on_date=date(2026, 10, 5)) == []


@pytest.mark.asyncio
async def test_pending_order_is_oldest_end_then_stable_slot_id(db):
    courses = await _seed(db, weeks=2, hours=1)
    # Timetable sorting puts this slot first by start time, but both end at 09:50.
    # A stable end-time tie must therefore use the occurrence's slot identity.
    courses.append({**courses[0], "id": "z_parallel_slot", "startTime": "08:50"})
    await ac.replace_schedule(db, courses)

    pending = await ac.pending_occurrences(db, datetime(2026, 9, 29, 12, 0))
    assert [(p["date"], p["slot_id"]) for p in pending] == [
        ("2026-09-21", courses[0]["id"]),
        ("2026-09-21", "z_parallel_slot"),
        ("2026-09-28", courses[0]["id"]),
        ("2026-09-28", "z_parallel_slot"),
    ]


@pytest.fixture
def ask_environment(db, monkeypatch):
    """Exercise the real skill and SQLite selection, replacing only clock and FCM."""
    @asynccontextmanager
    async def session_factory():
        yield db

    sent = []

    async def send_ask(fid, occurrence, token=None):
        sent.append((fid, occurrence, token))
        return True, "delivered"

    monkeypatch.setattr(attendance_skills, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(
        attendance_skills, "owner_now", lambda: datetime(2026, 9, 29, 12, 0)
    )
    monkeypatch.setattr(attendance_skills.fcm, "send_attendance_ask", send_ask)
    context = AgentContext(
        agent_id="ultron", user_id=1, session_id=1, request_id="attendance-recovery-test",
        triggered_by="user", trigger_payload={}, output_mode="respond", model="test",
        system_prompt="", conversation_history=[], db=db, timezone="Europe/Istanbul",
    )
    return context, sent


@pytest.mark.asyncio
async def test_manual_empty_ask_resends_oldest_without_settling_it(db, ask_environment):
    courses = await _seed(db, weeks=1, hours=2)
    await ac.register_device(db, "watch", "wear", "watch-fid", "watch-token")
    context, sent = ask_environment
    skill = attendance_skills.AskAttendanceSkill()

    result = await skill.execute({}, context)
    await skill.execute({}, context)

    assert len(sent) == 2
    assert all(p[1]["slot_id"] == courses[0]["id"] for p in sent)
    assert sent[0][2] == "watch-token"
    assert "2026-09-21" in result and "09:00 - 09:50" in result
    assert "submitted" in result.lower()
    assert await ac.list_attendance(db) == []

    # Only a real answer advances recovery to the next hour.
    await _answer(db, courses[0]["id"], TERM_START, "attended")
    await skill.execute({}, context)
    assert sent[-1][1]["slot_id"] == courses[1]["id"]


@pytest.mark.asyncio
async def test_manual_ask_selectors_target_the_requested_occurrence(db, ask_environment):
    courses = await _seed(db, weeks=2, hours=2)
    await ac.register_device(db, "watch", "wear", "watch-fid")
    context, sent = ask_environment

    await attendance_skills.AskAttendanceSkill().execute(
        {"course_code": "PHYS101", "date": "2026-09-28", "slot_id": courses[1]["id"]},
        context,
    )

    assert len(sent) == 1
    assert sent[0][1]["slot_id"] == courses[1]["id"]
    assert sent[0][1]["date"] == "2026-09-28"


@pytest.mark.asyncio
async def test_n8n_ask_does_not_replay_historical_backlog(db, ask_environment):
    await _seed(db, weeks=1)
    await ac.register_device(db, "watch", "wear", "watch-fid")
    context, sent = ask_environment
    context.triggered_by = "n8n"

    await attendance_skills.AskAttendanceSkill().execute({}, context)

    assert sent == []
    assert len(await ac.pending_occurrences(db, datetime(2026, 9, 29, 12, 0))) == 3


@pytest.mark.asyncio
async def test_manual_ask_without_watch_leaves_missing_hour_recoverable(db, ask_environment):
    await _seed(db, weeks=1, hours=1)
    context, sent = ask_environment

    result = await attendance_skills.AskAttendanceSkill().execute({}, context)

    assert sent == []
    assert "no active watch" in result.lower()
    assert await ac.list_attendance(db) == []
    assert len(await ac.pending_occurrences(db, datetime(2026, 9, 29, 12, 0))) == 1


@pytest.mark.asyncio
async def test_failed_manual_delivery_leaves_hour_recoverable(db, ask_environment, monkeypatch):
    await _seed(db, weeks=1, hours=1)
    await ac.register_device(db, "watch", "wear", "watch-fid")
    context, _ = ask_environment

    async def unavailable(*args, **kwargs):
        return False, "transport unavailable"

    monkeypatch.setattr(attendance_skills.fcm, "send_attendance_ask", unavailable)
    result = await attendance_skills.AskAttendanceSkill().execute({}, context)

    assert "transport unavailable" in result
    assert await ac.list_attendance(db) == []
    assert len(await ac.pending_occurrences(db, datetime(2026, 9, 29, 12, 0))) == 1
