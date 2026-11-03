# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Explicit CSV mapping with stable source occurrences; no fuzzy classification."""
import csv
import io
from hashlib import sha256
from sqlalchemy import select
from app.models.solus_ledger import SolusAccount
from app.schemas.solus import Post
from app.services.solus.commands import book_for, SolusError
from app.services.solus.money import minor


async def parse_csv(db, user_id, book_id, original):
    await book_for(db, user_id, book_id)
    if not isinstance(original, str) or len(original.encode()) > 1_000_000:
        raise SolusError("CSV must be text under 1 MB.")
    reader = csv.DictReader(io.StringIO(original.lstrip('\ufeff')), strict=True)
    required = {"date", "description", "kind", "amount", "debit_account", "credit_account", "currency", "event_ref"}
    optional = {"installments", "booked_on", "billed_on", "first_due_on", "document_number", "party"}
    if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames) or not required <= set(reader.fieldnames) or set(reader.fieldnames) - required - optional:
        raise SolusError("CSV needs unique headers: " + ", ".join(sorted(required)) + ". Optional: " + ", ".join(sorted(optional)))
    accounts = {(a.name, a.currency): a.id for a in (await db.execute(select(SolusAccount).where(SolusAccount.book_id == book_id))).scalars()}
    rows, identities = [], set()
    try:
        for index, row in enumerate(reader, 2):
            if len(rows) >= 500:
                raise SolusError("Import at most 500 rows at a time.")
            if None in row or any(v is None for v in row.values()):
                raise SolusError(f"CSV row {index} has missing or extra columns.")
            row = {k: v.strip() for k, v in row.items()}
            if not row["event_ref"] or row["event_ref"] in identities:
                raise SolusError(f"CSV row {index} needs a unique, stable event_ref.")
            identities.add(row["event_ref"])
            currency = row["currency"]
            minor(row["amount"], currency)
            debit, credit = accounts.get((row["debit_account"], currency)), accounts.get((row["credit_account"], currency))
            if not debit or not credit:
                raise SolusError(f"CSV row {index}: account name/currency does not match this book.")
            payload = {"operation": "post", "book_id": book_id, "command_key": f"import-row:{row['event_ref']}",
                "occurrence_key": f"import:{row['event_ref']}", "kind": row["kind"], "description": row["description"],
                "occurred_on": row["date"], "lines": [{"account_id": debit, "side": "debit", "amount": row["amount"]},
                    {"account_id": credit, "side": "credit", "amount": row["amount"]}]}
            if row.get("booked_on"):
                payload["booked_on"] = row["booked_on"]
            if row.get("billed_on"):
                payload["billed_on"] = row["billed_on"]
            if row.get("installments"):
                payload["installments"] = int(row["installments"])
            if row.get("first_due_on"):
                payload.update(first_due_on=row["first_due_on"], schedule_amount=row["amount"], schedule_currency=currency)
            if row.get("document_number") or row.get("party"):
                payload["document"] = {"number": row.get("document_number") or None, "party": row.get("party") or None}
            rows.append(Post.model_validate(payload))
    except csv.Error as exc:
        raise SolusError(f"Invalid CSV: {exc}") from None
    if not rows:
        raise SolusError("CSV contains no transactions.")
    return sha256(original.encode()).hexdigest(), rows
