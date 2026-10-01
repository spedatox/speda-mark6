# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Explicit operator-reviewed owner corrections, never an agent write tool.

The private plan contains exact before hashes, complete replacement payloads
and the owner's literal confirmations. Each unit preserves its original row,
then uses the normal memory_store transaction boundary. No provider is called.
"""

import hashlib
import json

from sqlalchemy import select, text

from app.models.memory_file import MemoryFile
from app.models.memory_source import MemoryIssue, MemorySource
from app.models.memory_write_receipt import MemoryWriteReceipt
from app.services.memory_states import parse, verify_source, version
from app.services.memory_store import MemoryWriteConflict, _mutate_in_txn


def stable_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


async def preserve_source(db, user_id: int, source: dict, plan_id: str) -> str:
    key = source["key"]
    ident = stable_hash([user_id, key])[:32]
    existing = (await db.execute(select(MemorySource).where(
        MemorySource.user_id == user_id, MemorySource.source_key == key,
    ))).scalar_one_or_none()
    if existing:
        if (existing.content != source["content"] or existing.original_row != source["original_row"]
                or existing.classification != source["classification"]):
            raise ValueError("Immutable source key was reused for different content or provenance.")
        return existing.id
    db.add(MemorySource(id=ident, user_id=user_id, source_key=key,
        original_path=source.get("original_path", ""), content=source["content"],
        content_hash=version(source["content"]), classification=source["classification"],
        original_row=source["original_row"], migration_id=plan_id))
    await db.flush()
    return ident


async def apply_owner_unit(db, *, user_id: int, unit: dict, sources: list[dict], plan_id: str) -> dict:
    """Commit one reviewed unit once; changed evidence/plan is a new unit.

    Source and original preservation, receipt, passages and graph commit with
    the document. A stale before hash rolls everything back, including source
    insertion. An exact completed rerun returns its durable receipt.
    """
    # The runner's plan_id hashes all sources and units. Keep the receipt key
    # stable, and additionally check immutable source bindings on resumption.
    request_id = "owner-review:" + stable_hash([plan_id, user_id, unit])[:48]
    try:
        receipt = (await db.execute(select(MemoryWriteReceipt).where(
            MemoryWriteReceipt.user_id == user_id,
            MemoryWriteReceipt.request_id == request_id,
            MemoryWriteReceipt.path == unit["path"],
        ))).scalar_one_or_none()
        if receipt:
            for source in sources:
                stored = (await db.execute(select(MemorySource).where(
                    MemorySource.user_id == user_id, MemorySource.source_key == source["key"],
                ))).scalar_one_or_none()
                if (stored is None or stored.content != source["content"]
                        or stored.original_row != source["original_row"]
                        or stored.classification != source["classification"]):
                    raise ValueError("Resume source bindings changed; use the original reviewed plan.")
            return {"path": unit["path"], "receipt_id": receipt.id, "resumed": True}
        row = (await db.execute(select(MemoryFile).where(
            MemoryFile.user_id == user_id, MemoryFile.path == unit["path"],
        ))).scalar_one_or_none()
        before = row.content if row else None
        if unit["before_hash"] != (version(before) if before is not None else "new"):
            raise MemoryWriteConflict("Reviewed payload is stale. Re-read the unit; never replay a whole plan.")
        source_refs = {}
        for source in sources:
            source_refs[source["key"]] = "source:" + await preserve_source(db, user_id, source, plan_id)
        evidence = [{"ref": source_refs[item["key"]], "quote": item["quote"]} for item in unit["evidence"]]
        after = unit["after"]
        # Deterministic placeholders let the same plan bind actual source IDs.
        for key, ref in source_refs.items():
            after = after.replace("{{source:" + key + "}}", ref)
        if "{{source:" in after:
            raise ValueError("Unresolved source placeholder in reviewed payload.")
        if before == after:
            raise ValueError("A reviewed correction must change the payload; do not create an empty receipt.")
        if unit["path"].startswith("/memories/states/"):
            record = parse(after)
            await verify_source(db, user_id, record["source"])
        if row:
            # Textual SQLite reads retain the original timestamp spelling;
            # ORM datetime conversion would normalize a space into 'T'.
            original_row = dict((await db.execute(text(
                "SELECT * FROM memory_files WHERE id=:id AND user_id=:user_id"
            ), {"id": row.id, "user_id": user_id})).mappings().one())
            await preserve_source(db, user_id, {
                "key": f"{plan_id}:before:{row.id}:{version(before)}",
                "original_path": row.path, "classification": "historical",
                "content": before, "original_row": original_row,
            }, plan_id)
        if unit.get("schedule_rename"):
            from app.models.academic import CourseSlot
            from app.models.user import User
            from app.services.course_identity import course_heading
            from sqlalchemy import func
            # The legacy academic ledger has no user discriminator. Fail closed
            # instead of changing a different owner's schedule in such a DB.
            if (await db.execute(select(func.count()).select_from(User))).scalar_one() != 1:
                raise ValueError("Legacy schedule rename requires a single-owner database.")
            code, name = course_heading(after)
            slots = (await db.execute(select(CourseSlot).where(
                CourseSlot.code == code, CourseSlot.active.is_(True),
            ).order_by(CourseSlot.id))).scalars().all()
            actual = [{"id": slot.id, "name": slot.name, "slot_id": slot.slot_id} for slot in slots]
            if actual != unit["schedule_rename"]["before"] or not name:
                raise MemoryWriteConflict("Schedule changed since owner review; preserve all slots and re-read.")
            for slot in slots:
                raw = dict((await db.execute(text(
                    "SELECT * FROM course_slots WHERE id=:id"
                ), {"id": slot.id})).mappings().one())
                await preserve_source(db, user_id, {
                    "key": f"{plan_id}:course-slot:{slot.id}:{stable_hash(raw)}",
                    "original_path": f"course-slot:{slot.id}", "classification": "historical",
                    "content": json.dumps(raw, ensure_ascii=False, sort_keys=True), "original_row": raw,
                }, plan_id)
                slot.name = name
        await _mutate_in_txn(db, user_id=user_id, path=unit["path"], before=before,
            after=after, author="owner", action="owner_confirmation", managed=True,
            evidence=evidence, request_id=request_id, migration_id=plan_id)
        for issue_key in unit.get("resolved_issues", []):
            issue = (await db.execute(select(MemoryIssue).where(
                MemoryIssue.user_id == user_id, MemoryIssue.issue_key == issue_key,
            ))).scalar_one()
            issue.status = "resolved"
            issue.refs = [*issue.refs, *(item["ref"] for item in evidence)]
            issue.detail += "\nResolved by explicit owner confirmation; original editions remain preserved."
        await db.commit()
        receipt = (await db.execute(select(MemoryWriteReceipt).where(
            MemoryWriteReceipt.user_id == user_id, MemoryWriteReceipt.request_id == request_id,
            MemoryWriteReceipt.path == unit["path"],
        ))).scalar_one_or_none()
        if receipt is None:
            raise ValueError("Reviewed unit must change a payload and produce a receipt.")
        return {"path": unit["path"], "receipt_id": receipt.id, "resumed": False}
    except Exception:
        await db.rollback()
        raise
