# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Ultron's attendance tools — reading the ledger, and asking the watch.

The math itself lives in services/academic.py; these are the agent-facing
surface over it, per Rule 1 and Rule 5.
"""

import logging
from datetime import date as date_cls

from app.core.clock import owner_now
from app.core.context import AgentContext
from app.database import AsyncSessionLocal
from app.services import academic as academic_service
from app.services import fcm
from app.skills.base import Skill

logger = logging.getLogger(__name__)

_RISK_LABEL = {
    "safe": "güvende",
    "warning": "dikkat",
    "critical": "son hak",
    "failed": "DEVAMSIZLIKTAN KALDI",
}


class AttendanceStatusSkill(Skill):
    name = "check_attendance"
    deferred = True
    search_keywords = "class lecture school attendance university course roll present absent"
    read_only = True  # Rule 9 — pure retrieval, safe to run in parallel
    description = (
        "Reads the owner's course attendance ledger and returns, per subject, how many more "
        "teaching hours he can miss before failing on devamsızlık. Use this whenever he asks "
        "about attendance, absences, 'kaç hakkım kaldı', whether he can skip a specific class, "
        "or whether it is safe to miss a day — and use it before advising him to skip anything, "
        "because the answer depends on numbers you cannot guess. It reflects the standard rule "
        "(14 teaching weeks, 70% attendance mandatory, cancelled classes removed from the "
        "denominator rather than counted as absences), configured per term. Do NOT use it to "
        "record a new absence (the watch does that) and do NOT use it to look up the timetable "
        "itself. Returns one line per subject with hours attended, absences used, absences "
        "remaining and a risk level, plus a count of teaching hours that have happened but have "
        "no answer recorded yet."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "course_code": {
                "type": "string",
                "description": "Optional subject code (e.g. PHYS101) to report on just one course.",
            }
        },
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        wanted = (args.get("course_code") or "").strip().upper()

        async with AsyncSessionLocal() as db:
            term = await academic_service.get_active_term(db)
            if term is None:
                return (
                    "No term is configured yet, so there is no attendance budget to report. "
                    "The schedule and term dates need to be set via PUT /academic/schedule first."
                )
            summaries = await academic_service.summarise(db)

        if not summaries:
            return "No courses are in the schedule, so there is nothing to report."

        if wanted:
            summaries = [s for s in summaries if s["course_code"] == wanted]
            if not summaries:
                return f"No course with code {wanted} is in the schedule."

        lines = [
            f"Dönem: {term.total_weeks} hafta, %{int(term.required_rate * 100)} devam zorunlu.",
            "",
        ]
        for s in summaries:
            remaining = s["remaining_absences"]
            risk = _RISK_LABEL.get(s["risk"], s["risk"])
            if remaining > 0:
                verdict = f"{remaining} saat hakkı kaldı"
            elif remaining == 0:
                verdict = "hakkı bitti — bir devamsızlık daha kalmasına yol açar"
            else:
                verdict = f"limiti {abs(remaining)} saat aştı"

            lines.append(
                f"{s['course_name']} ({s['course_code']}): {verdict} [{risk}]\n"
                f"  {s['attended_hours']} saat girdi, {s['absent_hours']}/{s['allowed_absences']} "
                f"devamsızlık kullanıldı, {s['cancelled_hours']} ders iptal oldu "
                f"({s['weekly_hours']} sa/hafta, dönem toplamı {s['effective_hours']} saat)"
            )
            if s["unanswered_hours"]:
                lines.append(
                    f"  ⚠ {s['unanswered_hours']} saat henüz cevaplanmadı — bu sayı eksik olabilir."
                )

        return "\n".join(lines)


class AskAttendanceSkill(Skill):
    name = "ask_attendance"
    deferred = True
    search_keywords = "class lecture school attendance university course ask prompt resend missed unanswered present absent"
    requires_network = True
    description = (
        "Re-sends a 'derse girdin mi?' question to the owner's watch for an ended, unanswered "
        "teaching hour, including missed questions from earlier days or weeks in the active term. "
        "Use it when he asks to resend a missed or dismissed attendance question; optionally "
        "target a course, date or slot, otherwise it selects the oldest unanswered hour and "
        "sends one question per call. n8n-triggered calls retain the recent-lecture window; "
        "manual recovery does not expire when that window closes. Do NOT use it for a general "
        "reminder, an unfinished lecture, an already answered hour, or to record attendance "
        "yourself — only the owner answers. Returns the exact course, date and time, how many "
        "pushes FCM accepted and any remaining matching hours or delivery failure; push "
        "acceptance does not prove the watch displayed a notification."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "window_minutes": {
                "type": "integer",
                "default": 20,
                "minimum": 1,
                "maximum": 1440,
                "description": (
                    "Recent-lecture window for n8n-triggered calls only. "
                    "Manual resends search all ended, unanswered hours in the active term."
                ),
            },
            "course_code": {
                "type": "string",
                "description": "Optional course code to recover (e.g. ATA101).",
            },
            "date": {
                "type": "string",
                "format": "date",
                "description": "Optional occurrence date in YYYY-MM-DD, in the owner's timezone.",
            },
            "slot_id": {
                "type": "string",
                "description": "Optional exact teaching-hour slot ID; combine with date to target one occurrence.",
            },
        },
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        try:
            on_date = date_cls.fromisoformat(args["date"]) if args.get("date") else None
            window = int(args.get("window_minutes", 20))
        except (TypeError, ValueError):
            return "Use a date in YYYY-MM-DD and a window_minutes integer between 1 and 1440."
        if not 1 <= window <= 1440:
            return "window_minutes must be between 1 and 1440."

        async with AsyncSessionLocal() as db:
            pending = await academic_service.pending_occurrences(
                db, owner_now(),
                course_code=(args.get("course_code") or "").strip(),
                on_date=on_date,
                slot_id=(args.get("slot_id") or "").strip(),
                window_minutes=window if context.triggered_by == "n8n" else None,
            )
            if not pending:
                if context.triggered_by == "n8n":
                    return "No lecture has just ended without an answer, so there is nothing to ask."
                return "No ended, unanswered teaching hour matches in the active term, so nothing was sent."
            occurrence = pending[0]
            label = (
                f"{occurrence['course_name']} ({occurrence['course_code']}, "
                f"{occurrence['date']}, {occurrence['time']}; slot_id={occurrence['slot_id']})"
            )

            devices = await academic_service.active_devices(db, platform="wear")
            if not devices:
                return (
                    f"No active watch is registered, so the question about {label} was not sent. "
                    "It remains unanswered; it can also be resolved in the watch's attendance history."
                )

            delivered = 0
            failures: list[str] = []
            for device in devices:
                ok, detail = await fcm.send_attendance_ask(
                    device.fid, occurrence, device.token
                )
                if ok:
                    delivered += 1
                elif detail == "unregistered":
                    await academic_service.deactivate_device(db, device.fid)
                    failures.append(f"{device.device_id}: no longer installed (deactivated)")
                else:
                    failures.append(f"{device.device_id}: {detail}")

        logger.info(
            "attendance_ask",
            extra={
                "request_id": context.request_id,
                "slot_id": occurrence["slot_id"],
                "date": occurrence["date"],
                "delivered": delivered,
            },
        )

        if delivered:
            result = f"Submitted the attendance question about {label} to FCM for {delivered} device(s)."
            if failures:
                result += f" Other device deliveries failed: {'; '.join(failures)}."
            if len(pending) > 1:
                result += f" {len(pending) - 1} other matching teaching hour(s) remain unanswered."
            return result
        return (
            f"Could not deliver the question about {label}. {'; '.join(failures)}. "
            "It remains unanswered and can be retried or resolved in the watch's attendance history."
        )
