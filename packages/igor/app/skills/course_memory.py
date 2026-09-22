# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Ultron's per-course durable memory surface.

Attendance remains structured data in ``services.academic``.  This skill keeps
the things a timetable cannot express: lecture takeaways, materials, deadlines,
and assessment results, scoped to the term in which they mattered.
"""

from datetime import date

from sqlalchemy import select

from app.core.context import AgentContext
from app.models.memory_file import MemoryFile
from app.services.memory_admission import EVIDENCE_SCHEMA, resolve_evidence
from app.services.memory_spec import course_path
from app.services.memory_store import MemoryWriteConflict, mutate_file
from app.services.memory_write import WriteRejected, ledger_append
from app.services.memory_schema import MemorySchemaViolation
from app.skills.base import Skill


_SECTIONS = frozenset({"Overview", "Materials", "Assessments", "Lecture Log"})


def _new_course(code: str, name: str) -> str:
    title = f"{code} — {name or 'Course record'}"
    return (
        f"# {title}\n\n"
        "## Overview\n\n"
        "- Course record created; add instructor, syllabus, and goals when known.\n\n"
        "## Materials\n\n"
        "- _(none recorded)_\n\n"
        "## Assessments\n\n"
        "- _(none recorded)_\n\n"
        "## Lecture Log\n"
    )


def _append_section(text: str, section: str, entry: str) -> str:
    lines = text.splitlines()
    try:
        idx = next(i for i, line in enumerate(lines) if line == f"## {section}")
    except StopIteration as exc:
        raise WriteRejected(f"Course record has no `{section}` section; restore its standard shape first.") from exc
    end = next((i for i in range(idx + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    placeholder = "- _(none recorded)_"
    if placeholder in lines[idx + 1:end]:
        lines.remove(placeholder)
        # The template has a blank line either side of its placeholder. Keep
        # exactly one after the heading before writing the first real entry.
        while idx + 1 < len(lines) and not lines[idx + 1].strip():
            del lines[idx + 1]
        lines[idx + 1:idx + 1] = ["", f"- {entry}", ""]
        return "\n".join(lines).rstrip() + "\n"
    while end > idx + 1 and not lines[end - 1].strip():
        end -= 1
    lines.insert(end, f"- {entry}")
    return "\n".join(lines).rstrip() + "\n"


class CourseMemorySkill(Skill):
    name = "record_course_memory"
    deferred = True
    search_keywords = "course class lecture notes syllabus assignment exam university academic"
    restricted_to = frozenset({"ultron"})
    description = (
        "Records durable knowledge for one university course in its own term-scoped memory file, "
        "such as `/memories/academic/courses/2026-2027-spring/ATA101.md`. Use it when a class "
        "provides a lecture takeaway, a study material, an assessment date or result, or stable "
        "course context that should still be available weeks later. It creates the course record "
        "with Overview, Materials, Assessments, and a dated Lecture Log, so notes from different "
        "courses and semesters never mix. Do NOT use it for attendance or the timetable; those "
        "remain in the structured academic ledger. Returns the exact memory path written, or a "
        "clear validation error without saving anything."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "term": {"type": "string", "description": "Term as YYYY-YYYY-spring, -summer, or -fall; e.g. 2026-2027-spring."},
            "course_code": {"type": "string", "description": "Course code from the timetable, e.g. ATA101."},
            "course_name": {"type": "string", "description": "Human course name, used when creating the record."},
            "section": {"type": "string", "enum": ["Overview", "Materials", "Assessments", "Lecture Log"], "description": "Where this knowledge belongs."},
            "entry": {"type": "string", "description": "One factual, concise note to retain."},
            "date": {"type": "string", "description": "Required for Lecture Log: the lecture date as YYYY-MM-DD. Defaults to today for a live user turn."},
            "evidence": EVIDENCE_SCHEMA,
        },
        "required": ["term", "course_code", "section", "entry", "evidence"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        section = (args.get("section") or "").strip()
        if section not in _SECTIONS:
            return "section must be Overview, Materials, Assessments, or Lecture Log."
        try:
            path = course_path(args.get("term", ""), args.get("course_code", ""))
        except ValueError as exc:
            return str(exc)
        entry = (args.get("entry") or "").strip()
        if not entry:
            return "entry cannot be empty."
        code = (args.get("course_code") or "").strip().upper()
        row = (await context.db.execute(select(MemoryFile).where(
            MemoryFile.user_id == context.user_id, MemoryFile.path == path
        ))).scalar_one_or_none()
        before = row.content if row else ""
        base = before or _new_course(code, (args.get("course_name") or "").strip())
        try:
            if section == "Lecture Log":
                stamp = (args.get("date") or date.today().isoformat()).strip()
                date.fromisoformat(stamp)
                after = ledger_append(base, path=path, key=stamp, lines_in=[entry])
            else:
                after = _append_section(base, section, entry)
            evidence = await resolve_evidence(context.db, context.user_id, args.get("evidence"), session_id=context.session_id)
            await mutate_file(context.db, user_id=context.user_id, path=path,
                              author=context.agent_id, action="course_memory",
                              before=before if row else None, after=after,
                              request_id=context.request_id, managed=True,
                              evidence=evidence, model=context.model)
        except (ValueError, WriteRejected, MemorySchemaViolation, MemoryWriteConflict) as exc:
            return str(exc)
        return f"Recorded in {path}."
