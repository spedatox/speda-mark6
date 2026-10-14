# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Non-committing deterministic projections in the shared memory transaction."""
import json
from datetime import datetime, timezone
from sqlalchemy import select
from app.models.memory_file import MemoryFile
from app.models.memory_write_receipt import MemoryWriteReceipt
from app.services.solus import reports


async def refresh(db, context, receipt, command, evidence, rationale):
    from app.services.memory_store import record_revision
    from app.services.memory_catalog import maintain_record
    from app.services.memory_passages import index_passages
    from app.services.memory_graph import index_memory_revision
    from app.services.memory_states import version
    period = getattr(command, "occurred_on", None)
    period = period.strftime("%Y-%m") if period else None
    # A backdated entry changes later balance snapshots too. Rebuild existing
    # book projections in this same transaction; never leave stale derived money.
    from sqlalchemy import or_
    existing = (await db.execute(select(MemoryFile).where(MemoryFile.user_id == context.user_id,
        or_(MemoryFile.path.endswith(f"/solus-{receipt['book_id']}.md"),
            MemoryFile.path.contains(f"/solus-{receipt['book_id']}-"))))).scalars().all()
    periods = {json.loads(file.content.split('<!-- solus-projection-v1 -->', 1)[1].strip())["period"]
        for file in existing if '<!-- solus-projection-v1 -->' in file.content}
    if period:
        periods.add(period)
    if getattr(command, "operation", None) == "import_commit":
        from app.models.solus_ledger import SolusEvent
        dates = (await db.execute(select(SolusEvent.occurred_on).where(SolusEvent.book_id == receipt['book_id'],
            SolusEvent.posted.is_(True)))).scalars()
        periods.update(d.strftime("%Y-%m") for d in dates)
    for value in sorted(periods):
        await refresh_period(db, context, receipt, value, evidence, rationale)


async def refresh_period(db, context, receipt, period, evidence, rationale):
    from app.services.memory_store import record_revision
    from app.services.memory_catalog import maintain_record
    from app.services.memory_passages import index_passages
    from app.services.memory_graph import index_memory_revision
    from app.services.memory_states import version
    report = await reports.summary(db, context.user_id, receipt["book_id"], period)
    # The full period in the stable address prevents 1926/2026 collisions while
    # keeping the shared monthly folder convention.
    path = f"/memories/finance/{period[5:7]}-{period[2:4]}/solus-{receipt['book_id']}-{period}.md"
    text = "# Solus posted accounting\n\n<!-- solus-projection-v1 -->\n\n" + json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    file = (await db.execute(select(MemoryFile).where(MemoryFile.user_id == context.user_id, MemoryFile.path == path))).scalar_one_or_none()
    before = file.content if file else ""
    if file:
        file.content, file.updated_at = text, datetime.now(timezone.utc)
    else:
        file = MemoryFile(user_id=context.user_id, path=path, content=text)
        db.add(file)
    await db.flush()
    metadata = await maintain_record(db, context.user_id, file)
    metadata.period, metadata.period_basis, metadata.date_precision = period, "accounting_period", "month"
    await index_passages(db, context.user_id, metadata.record_id, path, text)
    revision = await record_revision(db, user_id=context.user_id, path=path, author=context.agent_id,
        action="solus_projection", before=before, after=text, request_id=context.request_id, record_id=metadata.record_id)
    db.add(MemoryWriteReceipt(user_id=context.user_id, path=path, author=context.agent_id, request_id=context.request_id,
        before_hash=version(before), after_hash=version(text), evidence=evidence, rationale=rationale, record_id=metadata.record_id))
    await db.flush()
    await index_memory_revision(db, context.user_id, revision, evidence, metadata.record_id)
