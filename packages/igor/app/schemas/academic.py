"""
Wire schemas for the Ultron Wear pipe.

Mirrored on the watch by data/ScheduleWire.kt — field names and aliases here are
the contract. Changing one without changing the other silently degrades the
watch to its cached schedule, which is the failure mode hardest to notice
because everything keeps working until the timetable changes.
"""

from datetime import date as date_cls

from pydantic import BaseModel, Field


# ── Schedule (Igor → watch) ─────────────────────────────────────────────────

class TermOut(BaseModel):
    start_date: str
    total_weeks: int = 14
    required_rate: float = 0.70
    holidays: list[str] = Field(default_factory=list)


class CourseOut(BaseModel):
    id: str
    code: str
    name: str
    instructor: str = ""
    roomNumber: str = ""          # noqa: N815 — matches the watch's JSON key
    dayOfWeek: str                # noqa: N815
    startTime: str                # noqa: N815
    endTime: str                  # noqa: N815


class ScheduleOut(BaseModel):
    term: TermOut | None = None
    courses: list[CourseOut] = Field(default_factory=list)
    updated_at: int | None = None


# ── Attendance (bidirectional) ──────────────────────────────────────────────

class AttendanceRecordIn(BaseModel):
    slot_id: str = Field(max_length=96)
    course_code: str = Field(default="", max_length=32)
    date: date_cls
    # attended | absent | cancelled. Validated in services.academic rather than
    # with an Enum so an unknown value is rejected with a useful message instead
    # of a 422 the watch cannot act on.
    status: str = Field(max_length=16)
    recorded_at: int = 0


class AttendanceSyncRequest(BaseModel):
    device: str = Field(default="", max_length=64)
    records: list[AttendanceRecordIn] = Field(default_factory=list, max_length=2000)


class AttendanceRecordOut(BaseModel):
    slot_id: str
    course_code: str
    date: str
    status: str
    recorded_at: int


class AttendanceSyncResponse(BaseModel):
    # Keys ("slotId@date") the server accepted, so the watch can clear its
    # pending flag for exactly those and retry the rest.
    accepted: list[str] = Field(default_factory=list)
    # Records the server holds that the watch is missing or has an older answer
    # for. Same last-write-wins rule on both ends, so the two converge.
    records: list[AttendanceRecordOut] = Field(default_factory=list)


# ── Device registration ─────────────────────────────────────────────────────

class DeviceRegisterRequest(BaseModel):
    device: str = Field(max_length=64)
    platform: str = Field(default="wear", max_length=16)
    # Firebase Installation ID — see the note on models.academic.Device.
    fid: str = Field(max_length=255)


# ── Schedule upsert (owner → Igor) ──────────────────────────────────────────

class ScheduleUpsertRequest(BaseModel):
    """Replaces the whole schedule. Whole-replace rather than per-row patching
    because a timetable is edited as a document, and a partial update that
    leaves one stale slot behind produces attendance math nobody can debug."""

    term: TermOut
    courses: list[CourseOut] = Field(default_factory=list, max_length=200)
