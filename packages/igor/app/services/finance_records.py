# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Typed financial facts; markdown ledgers are deterministic views, never inputs."""
import json
import re
from datetime import date
from decimal import Decimal

ROOT = "/memories/finance/records/"
MARKER = "<!-- finance-record-v1 "
VIEW = "<!-- finance-projection-v1 -->"
TYPES = ("transaction", "balance", "report", "recurring")
MOVEMENTS = ("expense", "income", "transfer", "debt_payment", "loan_disbursement")
FIELDS = {
    "transaction": {"date", "movement", "amount", "currency", "account", "counterparty", "event_ref", "reported_on"},
    "balance": {"date", "amount", "currency", "account", "balance_kind"},
    "report": {"date", "report_ref", "body"},
    "recurring": {"effective_from", "effective_until", "amount", "currency", "frequency", "account", "due_day"},
}
BASE = {"id", "type", "description", "status", "evidence"}


def money(value):
    # No locale guessing: 4.914 could mean 4914 TRY or 4.914 units.
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9]\d*)\.\d{2}", value):
        raise ValueError("Amount must be a decimal STRING with two places, e.g. '4914.00'; no grouping separators. Use null when the amount is unknown.")
    if Decimal(value) < 0:
        raise ValueError("Use a nonnegative magnitude and the correct movement type.")
    return value


def validate(record):
    if not isinstance(record, dict) or record.get("type") not in TYPES:
        raise ValueError("Financial type must be transaction, balance, report or recurring.")
    extra = set(record) - BASE - FIELDS[record["type"]]
    if extra:
        raise ValueError(f"Fields do not belong to {record['type']}: {sorted(extra)}")
    for key in ("id", "description", "status"):
        if not isinstance(record.get(key), str) or not record[key].strip():
            raise ValueError(f"Financial record requires {key}.")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", record["id"]):
        raise ValueError("Record id must be a stable lowercase slug; reuse it to correct that record.")
    if record["status"] not in ("active", "void", "unverified"):
        raise ValueError("status must be active, void or unverified. A correction updates its existing id, never appends another debt.")
    for field in ("date", "reported_on", "effective_from", "effective_until"):
        if record.get(field):
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", record[field]):
                raise ValueError(f"{field} must be YYYY-MM-DD.")
            date.fromisoformat(record[field])
    if record["type"] != "recurring":
        if not record.get("date") and not (record["type"] == "transaction" and record.get("reported_on")):
            raise ValueError("date is mandatory. For a transaction with unknown occurrence date, use date=null and an evidenced reported_on date.")
        from app.core.clock import owner_today
        if (record.get("date") or record.get("reported_on")) > owner_today().isoformat():
            raise ValueError("A future transaction/balance/report is a plan, not an observed fact. Use a state or recurring rule.")
    if record["type"] in ("transaction", "balance", "recurring"):
        if "amount" not in record:
            raise ValueError("amount is required; null explicitly means unknown, never zero.")
        money(record["amount"])
        if not re.fullmatch(r"[A-Z]{3}", record.get("currency", "")):
            raise ValueError("Use an explicit three-letter currency, e.g. TRY.")
        if not isinstance(record.get("account"), str) or not record["account"].strip():
            raise ValueError("account is required; use 'unknown' if the source does not identify it.")
    if record["type"] == "transaction":
        if record.get("movement") not in MOVEMENTS or not record.get("event_ref"):
            raise ValueError("A transaction requires movement and an evidence-based event_ref identifying this occurrence.")
    if record["type"] == "balance" and record.get("balance_kind") not in ("asset", "liability"):
        raise ValueError("Balance requires asset or liability; it is a dated snapshot, not an expense or a report aggregate.")
    if record["type"] == "report" and (not record.get("report_ref") or not record.get("body")):
        raise ValueError("Report requires report_ref and body, and cannot carry a transaction amount.")
    if record["type"] == "recurring":
        if not record.get("effective_from") or record.get("frequency") not in ("monthly", "weekly", "annual"):
            raise ValueError("Recurring rule requires effective_from and frequency (monthly/weekly/annual). It is not an actual month's payment.")
        if record.get("effective_until") and record["effective_until"] < record["effective_from"]:
            raise ValueError("effective_until cannot precede effective_from.")
        if record.get("due_day") is not None and (type(record["due_day"]) is not int or not 1 <= record["due_day"] <= 31):
            raise ValueError("due_day must be 1..31 or null.")
    for key, value in record.items():
        if isinstance(value, str) and ("<!--" in value or "-->" in value):
            raise ValueError("Metadata delimiters cannot appear in fields.")
        if isinstance(value, str) and key != "body" and ("\n" in value or "|" in value):
            raise ValueError(f"{key} must be a single table-safe line.")
    if not isinstance(record.get("evidence"), list) or not record["evidence"]:
        raise ValueError("Every financial record needs source evidence.")


def encode(record):
    validate(record)
    return f"# {record['id']}\n\n{MARKER}{json.dumps(record, ensure_ascii=False, sort_keys=True)} -->\n\n" + "\n".join(
        f"- {key}: {value}" for key, value in sorted(record.items()) if key != "evidence"
    ) + "\n\n## Evidence\n" + "\n".join(f"- {e['ref']}: {e['quote']}" for e in record["evidence"]) + "\n"


def parse(content):
    line = next((x for x in content.splitlines() if x.startswith(MARKER)), "")
    if not line.endswith(" -->"):
        raise ValueError("Financial metadata missing; use finance_record.")
    record = json.loads(line[len(MARKER):-4])
    validate(record)
    if encode(record) != content:
        raise ValueError("Financial text and metadata disagree; regenerate through finance_record.")
    return record


def table(headers, rows):
    return "| " + " | ".join(headers) + " |\n| " + " | ".join("---" for _ in headers) + " |\n" + "".join(
        "| " + " | ".join(str(v if v is not None else "unknown").replace("|", "/").replace("\n", " ") for v in row) + " |\n" for row in rows)


def views(records, legacy_months=()):
    active = [r for r in records if r["status"] != "void"]
    result = {}
    # Include months containing voided transactions too: voiding the last row
    # must clear its old view, not leave yesterday's expense visible.
    months = sorted({(r.get("date") or r["reported_on"])[:7] for r in records if r["type"] == "transaction"} | set(legacy_months))
    for month in months:
        rows = sorted((r for r in active if r["type"] == "transaction" and (r.get("date") or r["reported_on"]).startswith(month)), key=lambda r: (r.get("date") or r["reported_on"], r["id"]))
        result[f"/memories/finance/ledger/{month}.md"] = f"# {month}\n\n{VIEW}\n\n" + (
            "Amounts with unknown source values are shown as unknown. Transfers, debt payments and loan proceeds are separate from spending/earned income.\n\n") + table(
            ["Date", "Reported on", "Status", "Kind", "Description", "Amount", "Currency", "Account", "Record"],
            [[r.get("date"), r.get("reported_on", r.get("date")), r["status"], r["movement"], r["description"], r["amount"], r["currency"], r["account"], r["id"]] for r in rows])
    for month in legacy_months:
        result[f"/memories/finance/ledger/{month}.md"] += f"\nUnreconciled historical source: /memories/finance/legacy/{month}.md. Do not sum that source with reconciled rows above; it can include the same events and outdated classifications.\n"
    result["/memories/finance/balances.md"] = "# Account Balances\n\n" + VIEW + "\n\nDated observations; do not present as live balances without newer evidence. Never add report aggregates to account debts.\n\n" + table(
        ["Status", "As of", "Account", "Kind", "Amount", "Currency", "Record"],
        [[r["status"], r["date"], r["account"], r["balance_kind"], r["amount"], r["currency"], r["id"]] for r in sorted(active, key=lambda r: r.get("date") or "", reverse=True) if r["type"] == "balance"])
    result["/memories/finance/reports.md"] = "# Credit Reports\n\n" + VIEW + "\n\n" + table(
        ["Status", "Date", "Reference", "Description", "Record"],
        [[r["status"], r["date"], r["report_ref"], r["description"], ROOT + r["id"] + ".md"] for r in active if r["type"] == "report"])
    result["/memories/finance/monthly-structure.md"] = "# Monthly Structure\n\n" + VIEW + "\n\nRecurring rules only; actual monthly activity lives in ledger/. Ended rules remain explicitly dated.\n\n" + table(
        ["Status", "Effective from", "Until", "Frequency", "Due day", "Rule", "Amount", "Currency", "Record"],
        [[r["status"], r["effective_from"], r.get("effective_until"), r["frequency"], r.get("due_day"), r["description"], r["amount"], r["currency"], r["id"]] for r in active if r["type"] == "recurring"])
    return result


def summarize(records, month):
    """Known, reconciled movements only. Reports/balances never enter flow totals."""
    if not isinstance(month, str) or not re.fullmatch(r"\d{4}-\d{2}", month):
        raise ValueError("summary requires month=YYYY-MM.")
    date.fromisoformat(month + "-01")
    totals, excluded, balances = {}, [], {}
    for r in records:
        validate(r)
        if r["type"] == "balance" and r["status"] == "active":
            key = (r["account"], r["currency"], r["balance_kind"])
            if key not in balances or r["date"] > balances[key]["as_of"]:
                balances[key] = {"account":r["account"], "currency":r["currency"],
                    "kind":r["balance_kind"], "as_of":r["date"], "amount":r["amount"], "record":r["id"]}
        if r["type"] != "transaction" or r["status"] == "void":
            continue
        if not (r.get("date") or r["reported_on"]).startswith(month):
            continue
        if r["status"] != "active" or r["amount"] is None or r.get("date") is None:
            excluded.append({"record":r["id"], "reason":"Unverified, unknown amount or unknown occurrence date; not included in period totals."})
            continue
        currency = totals.setdefault(r["currency"], {k:Decimal("0.00") for k in MOVEMENTS})
        currency[r["movement"]] += Decimal(r["amount"])
    return {"month":month,
        "totals":{currency:{kind:f"{amount:.2f}" for kind,amount in values.items()} for currency,values in totals.items()},
        "excluded":excluded, "latest_reported_balances":list(balances.values()),
        "scope":"Reconciled, dated transactions only. Legacy tables are not additive. Loan proceeds, transfers and repayments remain separate. Dated balances are not live balances."}


async def refresh_views(db, user_id, request_id=""):
    """Called INSIDE the record transaction. Never overwrite unmigrated history."""
    from sqlalchemy import select, update
    from app.models.memory_file import MemoryFile
    from app.services.memory_store import record_revision
    from datetime import datetime, timezone
    files = (await db.execute(select(MemoryFile).where(MemoryFile.user_id == user_id)
                             .execution_options(populate_existing=True))).scalars().all()
    records = [parse(f.content) for f in files if f.path.startswith(ROOT)]
    # Unique event keys are enforced by the database too, closing the race in
    # which two agents read the same absence and create two different ids.
    from app.models.finance_identity import FinanceIdentity
    identities = (await db.execute(select(FinanceIdentity).where(FinanceIdentity.user_id == user_id))).scalars().all()
    identities = {r.record_id: r for r in identities}
    for r in records:
        key = (r.get("event_ref") if r["type"] == "transaction" else
               r.get("report_ref") if r["type"] == "report" else
               f"{r['account']}:{r['date']}:{r['balance_kind']}" if r["type"] == "balance" else r["id"])
        event_key = f"{r['type']}:{key}"
        identity = identities.get(r["id"])
        if identity is None:
            db.add(FinanceIdentity(user_id=user_id, record_id=r["id"], event_key=event_key))
        elif identity.event_key != event_key:
            raise ValueError("A record's event identity is immutable; correct its fields without creating a different occurrence.")
    await db.flush()
    existing = {f.path: f for f in files}
    legacy_months = [f.path.rsplit("/",1)[-1][:-3] for f in files if f.path.startswith("/memories/finance/legacy/")]
    for path, after in views(records, legacy_months).items():
        file = existing.get(path)
        before = file.content if file else ""
        if file and VIEW not in before:
            # Deployment cannot silently replace legacy financial knowledge.
            raise ValueError(f"Finance migration required for {path}; record and views rolled back together.")
        if before == after:
            continue
        if file:
            result = await db.execute(update(MemoryFile).where(MemoryFile.user_id == user_id,
                MemoryFile.path == path, MemoryFile.content == before).values(content=after, updated_at=datetime.now(timezone.utc))
                .execution_options(synchronize_session="fetch"))
            if result.rowcount != 1:
                raise ValueError("Financial view changed concurrently; retry after rereading.")
        else:
            db.add(MemoryFile(user_id=user_id, path=path, content=after))
        await record_revision(db, user_id=user_id, path=path, author="finance_projection",
                              action="render", before=before, after=after, request_id=request_id)
