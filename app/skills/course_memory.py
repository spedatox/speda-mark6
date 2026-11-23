"""Read term-scoped course notes from the Iacon catalogue."""

import json
import re
from dataclasses import replace

from sqlalchemy import select

from app.models.iacon import IaconRecord
from app.services.academic import list_slots
from app.services.lexical import fold
from app.skills.base import Skill


_ROOT = "/memories/academic/courses/"
_TERM = r"[0-9]{4}-[0-9]{4}-(spring|summer|fall)"
_CODE = r"[A-Z]{2,8}[0-9]{2,4}[A-Z]?"
_HEADING = re.compile(rf"#\s+(?:{_CODE}\s+[-–—:]\s+)?(.+)")


def _name_key(name):
    return " ".join(re.findall(r"[^\W_]+", fold(name)))


class ReadCourseMemorySkill(Skill):
    name = "read_course_memory"
    restricted_to = frozenset({"ultron"})
    read_only = True
    description = (
        "Read the owner's saved course database, including lecture notes, overview, "
        "materials and assessments. Use it when asked about what the owner previously "
        "shared about a class, using either its code or its name. Use the timetable for "
        "schedules and the attendance ledger for attendance. Returns the saved record with its source and term, or lists "
        "matching courses when the request does not identify one record."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "course_code": {"type": "string", "maxLength": 32, "description": "Course code, e.g. MIS 231."},
            "course_name": {"type": "string", "maxLength": 200,
                            "description": "Course name from the timetable or archive, e.g. General Accounting. Use instead of course_code."},
            "term": {"type": "string", "maxLength": 24, "description": "Academic term, e.g. 2026-2027-fall."},
            "offset": {"type": "integer", "minimum": 0, "maximum": 1000000,
                       "description": "Continue from next_offset in the previous result."},
        },
        "additionalProperties": False,
    }

    def __init__(self, catalogue):
        self.catalogue = catalogue

    async def context_data(self, context):
        # Enrollment context is an automatic local read, not a test of whether
        # the answering model remembers to ask for the owner's saved courses.
        if context.agent_id != 'ultron' or context.task_role == 'worker':
            return None
        async with self.catalogue.sessions() as db:
            listing = json.loads(await self.execute({}, replace(context, db=db)))
            names = {}
            for slot in await list_slots(db):
                code = ''.join(slot.code.upper().split())
                if slot.name.strip():
                    names.setdefault(code, set()).add(slot.name.strip())
        for record in listing.get('records', []):
            code = record['path'].rsplit('/', 1)[-1].removesuffix('.md')
            if code in names:
                record['timetable_names'] = sorted(names[code])[:3]
        return {**listing, 'scope':'Saved course records by academic term; absence is not proof of non-enrollment.'}

    async def execute(self, args, context):
        code = "".join(args.get("course_code", "").upper().split())
        name = _name_key(args.get("course_name", ""))
        term = args.get("term", "").strip().lower().replace(" ", "-")
        offset = args.get("offset", 0)
        if code and not re.fullmatch(_CODE, code):
            return '{"status":"invalid_arguments","reason":"Use a course code such as MIS231."}'
        if term and not re.fullmatch(_TERM, term):
            return '{"status":"invalid_arguments","reason":"Use a term such as 2026-2027-fall."}'
        if "course_name" in args and (not name or code):
            return '{"status":"invalid_arguments","reason":"Use a course name or a course code."}'

        title = IaconRecord.title
        criteria = (
            IaconRecord.user_id == context.user_id,
            IaconRecord.kind == "document",
            IaconRecord.status == "accepted",
            title.startswith(_ROOT),
            title.regexp_match(rf"^{_ROOT}{term or _TERM}/{code or _CODE}\.md$"),
        )
        query = select(IaconRecord.id, title).where(*criteria).order_by(title.desc(), IaconRecord.id)
        if name:
            rows = await self._match_name(context, query, name)
            if len(rows) == 1:
                return await self._read(context.user_id, *rows[0], offset)
            match_status = "select_term" if len({path.rsplit("/", 1)[-1] for _, path in rows}) == 1 else "select_course"
            rows = rows[offset:offset + 41]
        else:
            match_status = "select_term" if code else "list"
            if code:
                # Determine ambiguity before applying a character offset to a unique record.
                rows = (await context.db.execute(query.limit(2))).all()
                if len(rows) == 1:
                    return await self._read(context.user_id, *rows[0], offset)
            rows = (await context.db.execute(query.offset(offset).limit(41))).all()

        return json.dumps({
            "status": match_status if rows else "no_matches",
            "records": [{"path": path, "record_id": record_id} for record_id, path in rows[:40]],
            "next_offset": offset + 40 if len(rows) > 40 else None,
        }, ensure_ascii=False)

    async def _match_name(self, context, query, name):
        schedule_codes = {
            "".join(slot.code.upper().split())
            for slot in await list_slots(context.db) if _name_key(slot.name) == name
        }
        rows = await context.db.execute(query.add_columns(IaconRecord.payload["content"].as_string()))
        matches = []
        for record_id, path, body in rows:
            heading = _HEADING.match((body or "").lstrip())
            code = path.rsplit("/", 1)[-1].removesuffix(".md")
            if code in schedule_codes or (heading and _name_key(heading[1]) == name):
                matches.append((record_id, path))
        return matches

    async def _read(self, user_id, record_id, path, offset):
        try:
            record = await self.catalogue.get(user_id, record_id)
        except KeyError:
            return '{"status":"unavailable"}'
        if record["status"] != "accepted" or record["evidence_status"] != "active":
            return '{"status":"withdrawn"}'
        if record["title"] != path:
            return '{"status":"unavailable","reason":"The course record moved. Look it up again."}'
        body = record["payload"]["content"]
        end = min(offset + 3000, len(body))
        return json.dumps({
            "status": "found",
            "record_id": record_id,
            "version": record["version"],
            "path": path,
            "evidence_scope": record["evidence_scope"],
            "source_ids": sorted({e["source_id"] for e in record["evidence"]}),
            "text": body[offset:end],
            "next_offset": end if end < len(body) else None,
        }, ensure_ascii=False)


_ALLOWED_SECTIONS = ("Overview", "Materials", "Assessments", "Lecture Log")


def _apply_section_update(body: str, section: str, entry: str, mode: str) -> str:
    """Append to or replace one named `## <section>` block while preserving all other sections."""
    cleaned_entry = entry.strip()
    if mode == "append" and not cleaned_entry.startswith(("-", "*", "###")):
        cleaned_entry = f"- {cleaned_entry}"
    header_re = re.compile(rf"(?m)^##[ \t]+{re.escape(section)}[ \t]*$")
    match = header_re.search(body)
    if not match:
        sep = "\n\n" if body.endswith("\n") else "\n\n"
        return body.rstrip() + f"{sep}## {section}\n\n{cleaned_entry}\n"
    next_sec = re.search(r"(?m)^##[ \t]+\S", body[match.end():])
    sec_end = match.end() + next_sec.start() if next_sec else len(body)
    existing_block = body[match.end():sec_end].strip()
    if mode == "replace_section":
        new_block = cleaned_entry
    else:
        new_block = f"{existing_block}\n{cleaned_entry}" if existing_block else cleaned_entry
    prefix = body[:match.end()].rstrip()
    suffix = body[sec_end:].lstrip("\n")
    rebuilt = f"{prefix}\n\n{new_block}\n"
    if suffix:
        rebuilt += f"\n{suffix}"
    return rebuilt


class UpdateCourseMemorySkill(Skill):
    name = "update_course_memory"
    restricted_to = frozenset({"ultron"})
    read_only = False
    description = (
        "Update one of the owner's saved course records (e.g. adding exam scores, assessments, "
        "materials, overview details, or lecture log entries) while preserving all existing notes. "
        "Use ONLY when the owner explicitly asks to record or update a course note in the current "
        "interactive user conversation; automatic and agent-triggered calls are refused. "
        "Read the record with read_course_memory first to get its current version (expected_version). "
        "Writes through the canonical Iacon catalogue with full revision history."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "course_code": {"type": "string", "maxLength": 32, "description": "Course code, e.g. MIS243 or MIS 215."},
            "term": {"type": "string", "maxLength": 24, "description": "Optional academic term, e.g. 2026-2027-fall."},
            "expected_version": {"type": "integer", "minimum": 1, "description": "Current record version from read_course_memory."},
            "section": {
                "type": "string",
                "enum": list(_ALLOWED_SECTIONS),
                "description": "Target markdown section inside the course record.",
            },
            "entry": {
                "type": "string",
                "minLength": 1,
                "maxLength": 2000,
                "description": "Concise factual entry to append (or replace the section with).",
            },
            "mode": {
                "type": "string",
                "enum": ["append", "replace_section"],
                "default": "append",
                "description": "Default 'append' preserves all existing notes in the section; use 'replace_section' only for explicit corrections.",
            },
        },
        "required": ["course_code", "expected_version", "section", "entry"],
        "additionalProperties": False,
    }

    def __init__(self, catalogue):
        self.catalogue = catalogue

    async def execute(self, args, context):
        if getattr(context, "triggered_by", None) != "user" or getattr(context, "task_role", "interactive") != "interactive":
            return json.dumps({
                "status": "refused",
                "reason": "Course record edits require an explicit request in an interactive owner conversation.",
            })
        code = "".join(str(args.get("course_code", "")).upper().split())
        term = str(args.get("term", "")).strip().lower().replace(" ", "-")
        section = str(args.get("section", "")).strip()
        entry = str(args.get("entry", "")).strip()
        mode = str(args.get("mode", "append")).strip()
        expected_version = int(args.get("expected_version", 0))

        if not code or not re.fullmatch(_CODE, code):
            return '{"status":"invalid_arguments","reason":"Use a course code such as MIS243."}'
        if term and not re.fullmatch(_TERM, term):
            return '{"status":"invalid_arguments","reason":"Use a term such as 2026-2027-fall."}'
        if section not in _ALLOWED_SECTIONS:
            return '{"status":"invalid_arguments","reason":"Choose Overview, Materials, Assessments, or Lecture Log."}'
        if not entry or len(entry) > 2000:
            return '{"status":"invalid_arguments","reason":"Entry must be 1..2000 characters."}'
        if mode not in {"append", "replace_section"}:
            return '{"status":"invalid_arguments","reason":"Mode must be append or replace_section."}'

        title_col = IaconRecord.title
        query = select(IaconRecord.id, title_col).where(
            IaconRecord.user_id == context.user_id,
            IaconRecord.kind == "document",
            IaconRecord.status == "accepted",
            title_col.startswith(_ROOT),
            title_col.regexp_match(rf"^{_ROOT}{term or _TERM}/{code}\.md$"),
        ).order_by(title_col.desc(), IaconRecord.id)

        async with self.catalogue.sessions() as db:
            rows = (await db.execute(query.limit(2))).all()
        if not rows:
            return json.dumps({"status": "not_found", "reason": f"No saved course record found for {code}."})
        if len(rows) > 1:
            return json.dumps({
                "status": "select_term",
                "reason": "Multiple terms match; pass the exact term.",
                "records": [{"path": p, "record_id": rid} for rid, p in rows],
            })

        record_id, path = rows[0]
        try:
            current = await self.catalogue.get(context.user_id, record_id)
        except KeyError:
            return '{"status":"unavailable"}'
        if current["version"] != expected_version:
            return json.dumps({
                "status": "version_conflict",
                "current_version": current["version"],
                "reason": "Course record changed since you last read it; call read_course_memory again.",
            })

        old_body = current["payload"]["content"]
        new_body = _apply_section_update(old_body, section, entry, mode)
        if new_body == old_body:
            return json.dumps({
                "status": "unchanged",
                "record_id": record_id,
                "version": current["version"],
                "path": path,
            })

        from app.schemas.iacon import Command
        from app.services.iacon_catalogue import Conflict
        from app.services.memory_store import digest

        content_hash = digest(new_body)[:16]
        intake = await self.catalogue.intake_source(
            context.user_id,
            key=f"course:{record_id}:v{expected_version}:{content_hash}",
            content=new_body,
        )
        new_payload = {**current["payload"], "content": new_body}
        cmd = Command(
            command_id=f"course-update:{record_id}:v{expected_version}:{content_hash}",
            record_id=record_id,
            expected_version=expected_version,
            kind="document",
            title=path,
            payload=new_payload,
            evidence=[{
                "source_id": intake["source_id"],
                "start": 0,
                "end": len(new_body),
                "quote": new_body,
            }],
        )
        try:
            saved = await self.catalogue.apply(context.user_id, cmd, actor="owner")
        except Conflict as exc:
            return json.dumps({"status": "version_conflict", "reason": str(exc)})
        except ValueError as exc:
            return json.dumps({"status": "error", "reason": str(exc)})

        return json.dumps({
            "status": "updated",
            "record_id": saved["id"],
            "version": saved["version"],
            "path": path,
            "section": section,
            "mode": mode,
        }, ensure_ascii=False)
