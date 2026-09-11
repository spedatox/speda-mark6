# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
import json
from sqlalchemy import select
from app.models.memory_file import MemoryFile
from app.services import finance_records as finance
from app.services.memory_admission import EVIDENCE_SCHEMA, resolve_evidence
from app.services.memory_states import version
from app.services.memory_store import mutate_file
from app.skills.base import Skill
from app.services.memory_schema import MemorySchemaViolation


class FinanceRecordSkill(Skill):
    name = "finance_record"
    read_only = False
    restricted_to = frozenset({"sentinel", "orion"})
    description = (
        "The ONLY financial activity writer. summary(month=YYYY-MM) calculates exact "
        "reconciled totals by movement/currency, with exclusions and dated balances. "
        "Use it for reports; never add legacy tables to these totals. get/list before put. One stable id per "
        "actual transaction, dated account balance, source report, or recurring rule. "
        "Correct by updating its id with the current version; void invalid records. "
        "The monthly ledger, balances, reports index and monthly-structure are computed. "
        "transaction: date,movement,amount,currency,account,event_ref (this occurrence's source identity). "
        "balance: date(as of),amount,currency,account,balance_kind. "
        "report: date,report_ref,body; no monetary amount field. "
        "recurring: effective_from,frequency,amount,currency,account; optional effective_until,due_day. "
        "All require id,type,description,status(active/void),evidence[{ref,quote}]. "
        "Unknown transaction date: date=null, reported_on=owner report date. If the occurrence month is evidenced, period=YYYY-MM permits month-precision totals. Unverified claims use status=unverified. "
        "Amounts are strings e.g. 4914.00, NEVER 4.914 or 4,914.00. Unknown is null. "
        "A card balance is not an item purchase price. A repayment/transfer/loan "
        "disbursement is not ordinary spending/income. A credit report is not another debt."
    )
    input_schema = {"type": "object", "properties": {
        "operation": {"type": "string", "enum": ["get", "list", "put", "summary"]},
        "month": {"type": "string"},
        "id": {"type": "string"}, "version": {"type": "string"},
        "record": {"type": "object", "properties": {
            **{k: {"type": "string"} for k in ("id", "description", "reported_on", "period", "currency", "account", "counterparty", "event_ref", "report_ref", "body", "effective_from", "effective_until")},
            "date": {"type": ["string", "null"]},
            "type": {"type": "string", "enum": list(finance.TYPES)},
            "status": {"type": "string", "enum": ["active", "void", "unverified"]},
            "movement": {"type": "string", "enum": list(finance.MOVEMENTS)},
            "balance_kind": {"type": "string", "enum": ["asset", "liability"]},
            "frequency": {"type": "string", "enum": ["weekly", "monthly", "annual"]},
            "due_day": {"type": ["integer", "null"]},
            "amount": {"type": ["string", "null"]},
            "evidence": EVIDENCE_SCHEMA,
        }, "required": ["id", "type", "description", "status", "evidence"], "additionalProperties": False},
    }, "required": ["operation"], "additionalProperties": False}

    async def execute(self, args, context):
        if context.agent_id not in self.restricted_to:
            return "Write rejected — finance belongs to Sentinel; hand off with source evidence."
        try:
            files = (await context.db.execute(select(MemoryFile).where(
                MemoryFile.user_id == context.user_id, MemoryFile.path.startswith(finance.ROOT),
            ).execution_options(populate_existing=True))).scalars().all()
            if args.get("operation") == "list":
                return json.dumps([{"record": finance.parse(f.content), "version": version(f.content)} for f in files], ensure_ascii=False)
            if args.get("operation") == "summary":
                return json.dumps(finance.summarize([finance.parse(f.content) for f in files], args.get("month")), ensure_ascii=False)
            record = dict(args.get("record") or {})
            ident = record.get("id") if args.get("operation") == "put" else args.get("id")
            path = f"{finance.ROOT}{ident}.md"
            file = next((f for f in files if f.path == path), None)
            before = file.content if file else None
            expected = version(before) if before is not None else "new"
            if args.get("operation") == "get":
                return json.dumps({"path": path, "version": expected, "record": finance.parse(before) if before else None}, ensure_ascii=False)
            if args.get("operation") != "put" or args.get("version") != expected:
                raise ValueError("Missing/stale version. get the existing record or use version=new only for a new event.")
            evidence = await resolve_evidence(context.db, context.user_id, record.get("evidence"), session_id=context.session_id)
            record["evidence"] = [{"ref": e["ref"], "quote": e["quote"]} for e in evidence]
            finance.validate(record)
            for f in files:
                other = finance.parse(f.content)
                if other["id"] == record["id"]:
                    if other["type"] != record["type"]:
                        raise ValueError("A record cannot change financial type; void the mistaken record and create its correctly typed replacement with evidence.")
                    continue
                if record["type"] == other["type"] == "transaction" and other.get("event_ref") == record.get("event_ref"):
                    raise ValueError(f"This occurrence already exists as {other['id']}; update it instead of duplicating it.")
                if record["type"] == other["type"] == "report" and other.get("report_ref") == record.get("report_ref"):
                    raise ValueError(f"This report already exists as {other['id']}; correct that record.")
            after = finance.encode(record)
            await mutate_file(context.db, user_id=context.user_id, path=path, before=before,
                              after=after, author=context.agent_id, action="finance_record",
                              request_id=context.request_id, managed=True, evidence=evidence, model=context.model)
            return json.dumps({"written": path, "version": version(after), "views": "updated atomically"})
        except (ValueError, MemorySchemaViolation) as exc:
            return "Write rejected — " + str(exc)
