"""
Ultron's schedule-authoring tool.

The owner tells Ultron his timetable in chat; this saves it as the source of
truth in the same shape services/academic.py and routers/academic.py already
speak (see schemas/academic.py, mirrored on the watch by data/ScheduleWire.kt),
then pushes every registered Ultron Wear watch to refresh immediately rather
than waiting on its own six-hour background sync (sync/SyncScheduler.kt).
"""

import logging
from datetime import date as date_cls

from app.core.context import AgentContext
from app.database import AsyncSessionLocal
from app.services import academic as academic_service
from app.services import fcm
from app.skills.base import Skill

logger = logging.getLogger(__name__)

_WEEKDAYS = {
    "MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY",
}


class SaveScheduleSkill(Skill):
    name = "save_schedule"
    deferred = True
    search_keywords = "timetable ders programı schedule courses classes term semester watch wear sync push"
    requires_network = True  # the DB write is local, but the point of this tool is the FCM push after it
    description = (
        "Replaces the owner's whole course timetable — term dates plus every weekly teaching "
        "hour — and immediately pushes Ultron Wear (his Galaxy Watch 6) to refresh, instead of "
        "the watch waiting up to six hours for its own background sync. Use this whenever he "
        "gives you his schedule for the term, or a room/time/instructor change to an existing "
        "one — it is a full replace, so always pass every course still in effect, not just the "
        "one that changed. Do NOT use it to record an absence or answer 'derse girdin mi?' (that "
        "ledger is written from the watch itself and read back by check_attendance), and do NOT "
        "use it just to look up the current timetable. Returns the number of course slots saved "
        "and how many watches were reached, or an explanation when no watch is registered yet — "
        "the save itself still succeeds in that case, and the watch picks it up next time it opens."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "term": {
                "type": "object",
                "description": "Term configuration the attendance budget is computed against.",
                "properties": {
                    "start_date": {
                        "type": "string",
                        "description": "ISO date (YYYY-MM-DD) of the Monday of week 1.",
                    },
                    "total_weeks": {
                        "type": "integer",
                        "default": 14,
                        "description": "Teaching weeks in the term. Official holidays are removed separately, below.",
                    },
                    "required_rate": {
                        "type": "number",
                        "default": 0.70,
                        "description": "Minimum attendance fraction, e.g. 0.70 for the standard 70% devam zorunluluğu.",
                    },
                    "holidays": {
                        "type": "array",
                        "items": {"type": "string"},
                        "default": [],
                        "description": "ISO dates (YYYY-MM-DD) removed from every course's denominator — official holidays only, never an individual class cancellation.",
                    },
                },
                "required": ["start_date"],
            },
            "courses": {
                "type": "array",
                "description": (
                    "Every weekly teaching hour in the term. A 3-hour lecture on one day is "
                    "still one row (use startTime/endTime for the span); a course meeting on "
                    "two different days is two rows sharing the same 'code'."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": (
                                "Unique per weekly slot, e.g. 'phys101_tue_0900'. Stable across "
                                "saves — reusing the same id for the same slot keeps that slot's "
                                "attendance history; a new id starts a fresh one."
                            ),
                        },
                        "code": {
                            "type": "string",
                            "description": "Subject code shared by every slot of the same course, e.g. 'PHYS101'. Two different courses must never share a code.",
                        },
                        "name": {"type": "string"},
                        "instructor": {"type": "string", "default": ""},
                        "roomNumber": {"type": "string", "default": ""},
                        "dayOfWeek": {
                            "type": "string",
                            "enum": sorted(_WEEKDAYS),
                        },
                        "startTime": {"type": "string", "description": "24h HH:MM."},
                        "endTime": {"type": "string", "description": "24h HH:MM."},
                    },
                    "required": ["id", "code", "name", "dayOfWeek", "startTime", "endTime"],
                },
            },
        },
        "required": ["term", "courses"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        term = args.get("term") or {}
        courses = args.get("courses") or []

        start_date_raw = str(term.get("start_date") or "")
        try:
            start_date = date_cls.fromisoformat(start_date_raw)
        except ValueError:
            return f"'{start_date_raw}' is not a valid ISO date (YYYY-MM-DD) for term.start_date."

        if not courses:
            return (
                "No courses were given — pass every weekly teaching hour still in effect, "
                "not just the one that changed. This call replaces the whole timetable."
            )

        bad_days = sorted({
            str(c.get("dayOfWeek", "")).upper()
            for c in courses
            if str(c.get("dayOfWeek", "")).upper() not in _WEEKDAYS
        })
        if bad_days:
            return f"Invalid dayOfWeek value(s): {', '.join(bad_days)}. Must be one of {', '.join(sorted(_WEEKDAYS))}."

        ids = [c.get("id") for c in courses]
        if len(ids) != len(set(ids)):
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            return f"Duplicate course id(s): {', '.join(dupes)}. Each weekly slot needs its own id."

        async with AsyncSessionLocal() as db:
            await academic_service.upsert_term(
                db,
                start_date=start_date,
                total_weeks=int(term.get("total_weeks") or 14),
                required_rate=float(term.get("required_rate") or 0.70),
                holidays=list(term.get("holidays") or []),
            )
            count = await academic_service.replace_schedule(db, courses)

            # Same device loop as AskAttendanceSkill / NotificationsSkill: push
            # every active watch to resync now instead of on its own heartbeat.
            devices = await academic_service.active_devices(db, platform="wear")
            delivered = 0
            failures: list[str] = []
            for device in devices:
                ok, detail = await fcm.send_data_message(
                    fid=device.fid, data={"type": "sync_request"}, priority="high",
                )
                if ok:
                    delivered += 1
                elif detail == "unregistered":
                    await academic_service.deactivate_device(db, device.fid)
                    failures.append(f"{device.device_id}: no longer installed (deactivated)")
                else:
                    failures.append(f"{device.device_id}: {detail}")

        logger.info(
            "schedule_saved",
            extra={
                "request_id": context.request_id,
                "courses": count,
                "delivered": delivered,
            },
        )

        summary = f"Saved {count} course slot(s) for the term starting {start_date.isoformat()}."
        if delivered:
            return f"{summary} Pushed a refresh to {delivered} watch(es)."
        if devices:
            return (
                f"{summary} Could not reach any watch right now ({'; '.join(failures)}) — "
                "it will pick this up next time it opens, or on its own 6-hour sync."
            )
        return f"{summary} No watch is registered yet, so nothing was pushed — it will load this the first time the app is opened."
