# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Mechanical passage indexing; stable event/log addresses without extraction."""
import re
import uuid
from datetime import date
from sqlalchemy import select
from app.models.memory_passage import MemoryPassage


def passages(record_id: str, text: str):
    lines = text.splitlines(keepends=True)
    starts = {0}
    fenced = False
    for i, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        if not fenced and (re.match(r"^#{1,3}\s", line) or re.match(r"^- \[\d{4}-\d{2}-\d{2}", line)):
            starts.add(i)
    points = sorted(starts) + [len(lines)]
    for start, end in zip(points, points[1:]):
        body = "".join(lines[start:end])
        if not body.strip():
            continue
        found = re.search(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)", lines[start])
        mentioned_on = None
        if found:
            try:
                mentioned_on = date.fromisoformat(found[1])
            except ValueError:
                pass
        # Line reordering or path changes do not create a new occurrence.
        ident = uuid.uuid5(uuid.NAMESPACE_URL, "memory-passage:"+record_id+":"+body.strip()).hex
        yield {"id": ident, "kind": "dated_entry" if mentioned_on else "section",
               "text": body, "mentioned_on": mentioned_on, "start_line": start+1, "end_line": end}


async def index_passages(db, user_id: int, record_id: str, path: str, content: str):
    from app.services.memory_graph import add_edge
    from app.models.memory_entity import MemoryEntity
    old = (await db.execute(select(MemoryPassage).where(
        MemoryPassage.user_id == user_id, MemoryPassage.record_id == record_id,
    ))).scalars().all()
    by_id = {p.id: p for p in old}
    for row in old:
        row.retired = True
    entities = (await db.execute(select(MemoryEntity).where(MemoryEntity.user_id == user_id))).scalars().all()
    for part in passages(record_id, content):
        row = by_id.get(part["id"])
        if row is None:
            row = MemoryPassage(user_id=user_id, record_id=record_id, path=path, retired=False, **part)
            db.add(row)
        else:
            row.retired, row.path = False, path
            row.start_line, row.end_line = part["start_line"], part["end_line"]
        ref = "passage:"+part["id"]
        await add_edge(db, user_id, ref, "record:"+record_id, "part_of")
        for entity in entities:
            names = [entity.canonical_name, *(entity.aliases if isinstance(entity.aliases, list) else [])]
            if any(len(n) >= 4 and re.search(rf"(?<!\w){re.escape(n)}(?!\w)", part["text"], re.IGNORECASE) for n in names):
                await add_edge(db, user_id, ref, "entity:"+entity.id, "mentions", ref)
