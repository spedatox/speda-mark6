"""
Ultron's attendance tools — reading the ledger, and asking the watch.

The math itself lives in services/academic.py; these are the agent-facing
surface over it, per Rule 1 and Rule 5.
"""

import logging
from datetime import datetime

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
    search_keywords = "class lecture school attendance university course ask prompt present absent"
    requires_network = True
    description = (
        "Pushes a 'derse girdin mi?' question to the owner's watch for a teaching hour that has "
        "just ended, so he can answer with one tap and the ledger stays accurate. This is normally "
        "invoked by the n8n per-lecture trigger rather than chosen conversationally; use it "
        "directly only when he asks you to re-send a question he missed or dismissed. Do NOT use "
        "it to send a general reminder (use send_push_notification), and do NOT use it for a class "
        "that has not finished yet — the question only makes sense after the bell. Returns which "
        "occurrence was asked about and how many devices it reached, or an explanation when there "
        "is nothing to ask, which is the normal outcome most of the time it runs."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "window_minutes": {
                "type": "integer",
                "default": 20,
                "description": (
                    "How long after a lecture ends it is still worth asking about. "
                    "Beyond this the question is stale and the watch's local fallback "
                    "will have raised it already."
                ),
            }
        },
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        window = int(args.get("window_minutes") or 20)

        async with AsyncSessionLocal() as db:
            occurrence = await academic_service.occurrence_just_ended(
                # Wall-clock comparison against the timetable — see core/clock.py.
                db, owner_now(), window_minutes=window
            )
            if occurrence is None:
                return "No lecture has just ended without an answer, so there is nothing to ask."

            devices = await academic_service.active_devices(db, platform="wear")
            if not devices:
                return (
                    "A lecture just ended but no watch is registered, so the question cannot be "
                    "delivered. The watch will ask locally from its cached schedule instead."
                )

            delivered = 0
            failures: list[str] = []
            for device in devices:
                ok, detail = await fcm.send_attendance_ask(device.fid, occurrence)
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
                "delivered": delivered,
            },
        )

        label = f"{occurrence['course_name']} ({occurrence['time']}, {occurrence['date']})"
        if delivered:
            return f"Asked about {label} on {delivered} device(s)."
        return (
            f"Could not deliver the question about {label}. {'; '.join(failures)} "
            "The watch's local fallback will ask instead."
        )
