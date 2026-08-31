# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Ultron's academic tables — the schedule the watch renders and the attendance
ledger it writes back to.

See docs/ULTRON_WEAR.md for the whole pipeline (watch ⇄ Igor ⇄ n8n ⇄ FCM).
"""

from datetime import date as date_cls
from datetime import datetime

from sqlalchemy import Boolean, Date, Float, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CourseSlot(Base):
    """
    One **ders saati** — a single teaching hour in a single weekly slot.

    The finest grain on purpose. A three-hour Tuesday course is three rows, not
    one row with a duration, because Turkish attendance is counted per hour: you
    can attend the 09:00 and miss the 11:00, and the yoklama records exactly one
    absence. `code` groups the rows that share one attendance budget.

    Mirrored on the watch by data/Course.kt.
    """

    __tablename__ = "course_slots"
    __table_args__ = (
        UniqueConstraint("slot_id", name="uq_course_slot_id"),
        Index("ix_course_slots_code", "code"),
        Index("ix_course_slots_day", "day_of_week"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # Stable across schedule edits — the join key the ledger and the watch use.
    slot_id: Mapped[str] = mapped_column(String(96))
    # Subject code (PHYS101). Two different courses must never share one.
    code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(160))
    instructor: Mapped[str] = mapped_column(String(160), default="")
    room: Mapped[str] = mapped_column(String(64), default="")

    # "MONDAY".."SUNDAY" — java.time.DayOfWeek names, so the watch parses them
    # with valueOf() and no mapping table exists on either side.
    day_of_week: Mapped[str] = mapped_column(String(16))
    # "HH:MM", 24h. Stored as text because they are wall-clock times on a
    # timetable, not instants; a DateTime here would invite a timezone.
    start_time: Mapped[str] = mapped_column(String(5))
    end_time: Mapped[str] = mapped_column(String(5))

    active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class TermConfig(Base):
    """
    Semester parameters. One active row at a time; older rows are kept so a past
    semester's verdicts stay reproducible.

    Defaults encode the standard Turkish undergraduate rule — 14 teaching weeks,
    70% attendance mandatory — but both are configurable because summer school,
    12-week terms and 80%-attendance labs all exist.
    """

    __tablename__ = "term_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Monday of week 1. Every week number counts from here.
    start_date: Mapped[date_cls] = mapped_column(Date)
    total_weeks: Mapped[int] = mapped_column(Integer, default=14)
    required_rate: Mapped[float] = mapped_column(Float, default=0.70)
    # Comma-separated ISO dates with no teaching (resmî tatil, bayram, ara
    # tatil). Occurrences on these dates leave the denominator entirely.
    holidays: Mapped[str] = mapped_column(String(1024), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    @property
    def holiday_list(self) -> list[str]:
        return [h for h in (self.holidays or "").split(",") if h.strip()]


class AttendanceEntry(Base):
    """
    One answer to one "derse girdin mi?".

    Identity is (slot_id, date) — re-answering an occurrence UPDATES, never
    appends, because a duplicate would double-count against the absence budget
    and quietly fail a course the owner is actually passing.

    `status` is one of attended | absent | cancelled. `cancelled` is NOT a third
    flavour of absence: it is the instructor not holding the class, which removes
    that hour from the denominator. Conflating the two is the single easiest way
    to make this feature lie, so the distinction is preserved in the schema, the
    math and the wire format alike.
    """

    __tablename__ = "attendance_entries"
    __table_args__ = (
        UniqueConstraint("slot_id", "date", name="uq_attendance_occurrence"),
        Index("ix_attendance_code_date", "course_code", "date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    slot_id: Mapped[str] = mapped_column(String(96))
    # Denormalised from CourseSlot so a course dropped mid-semester keeps its
    # history — those absences were still real.
    course_code: Mapped[str] = mapped_column(String(32))
    date: Mapped[date_cls] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16))

    # Epoch millis from the recording device. Last-write-wins conflict
    # resolution between the watch and the server uses this, not row order.
    recorded_at: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(32), default="wear")
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class Device(Base):
    """
    A device Igor can push to.

    `fid` is the **Firebase Installation ID**, not a registration token.
    firebase-messaging 25.1.0 deprecated getToken/deleteToken/onNewToken, and the
    Admin SDKs deprecated `Message(token=…)` in favour of `Message(fid=…)`;
    building on the token API today would mean migrating mid-semester.
    """

    __tablename__ = "devices"
    __table_args__ = (UniqueConstraint("device_id", name="uq_device_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[str] = mapped_column(String(64))
    platform: Mapped[str] = mapped_column(String(16), default="wear")
    fid: Mapped[str] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
