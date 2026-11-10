"""Read term-scoped course notes from the Iacon catalogue."""

import json
import re

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
