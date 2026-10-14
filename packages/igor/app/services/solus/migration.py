# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read-only inventory: legacy evidence is never silently converted into money."""
from hashlib import sha256
import json
from sqlalchemy import select
from app.models.memory_file import MemoryFile
from app.models.solus import SolusTransaction
from app.services import finance_records


async def inventory(db, user_id):
    files = (await db.execute(select(MemoryFile).where(MemoryFile.user_id == user_id,
        MemoryFile.path.startswith("/memories/finance/")).order_by(MemoryFile.path))).scalars().all()
    items = []
    for file in files:
        if "solus-projection-v1" in file.content:
            continue
        record = None
        try:
            if finance_records.MARKER in file.content:
                record = finance_records.parse(file.content)
        except (ValueError, KeyError):
            pass
        items.append({"source": file.path, "sha256": sha256(file.content.encode()).hexdigest(),
            "original": file.content, "record": record, "disposition": "needs owner mapping; excluded from Solus totals"})
    prototypes = (await db.execute(select(SolusTransaction).where(SolusTransaction.user_id == user_id).order_by(SolusTransaction.id))).scalars().all()
    for row in prototypes:
        original = {"id": row.id, "entity": row.entity, "type": row.transaction_type, "amount": str(row.total_amount),
            "currency": row.currency, "date": row.date.isoformat(), "installments": row.installment_count}
        encoded = json.dumps(original, sort_keys=True)
        items.append({"source": f"prototype:{row.id}", "sha256": sha256(encoded.encode()).hexdigest(),
            "original": encoded, "record": original, "disposition": "needs account and card mapping; excluded from Solus totals"})
    return {"items": items, "scope": "Preserved historical sources. No invented account, date, tax, exchange rate or payment."}
