# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
import json
from pydantic import TypeAdapter
from app.skills.base import Skill
from app.schemas.solus import SolusCommandInput
from app.services.memory_store import run_financial_command
from app.services.solus import reports


class SolusAccountingSkill(Skill):
    name = "solus_accounting"
    read_only = False
    memoize = False
    deferred = True
    restricted_to = frozenset({"sentinel", "orion", "speda"})
    description = (
        "Read and write Solus double-entry books, accounts, journals, obligations and credit-card statements. "
        "Read books/accounts before constructing commands; amounts are exact decimal strings and each retry must reuse its command_key and occurrence_key. "
        "Use write with a schema-valid command and original evidence only for an authorized, dated occurrence; never infer a payment, missing amount, tax, exchange rate or card cutoff. "
        "Use statement for billed card installments and supplied closing/due dates, summary for actual occurrence-date totals, and forecast for unpaid obligations and plans. "
        "Returns book-scoped native-currency reports or an atomic receipt; corrections use reverse rather than deletion. "
        "Personal and business books are separate; legacy finance_record remains available during owner-managed migration. Never combine its totals with Solus totals or dual-write an occurrence."
    )
    _command_schema = TypeAdapter(SolusCommandInput).json_schema()
    _command_defs = _command_schema.pop("$defs", {})
    input_schema = {
        "$defs": _command_defs,
        "type": "object", "properties": {
            "operation": {"type": "string", "enum": ["books", "accounts", "events", "summary", "schedules", "plans", "statement", "forecast", "business", "positions", "reconciliations", "write"]},
            "book_id": {"type": "string"}, "period": {"type": "string", "description": "YYYY-MM; required for summary, forecast, statement"},
            "account_id": {"type": "string"}, "cursor": {"type": "string"},
            "command": _command_schema,
        }, "required": ["operation"], "additionalProperties": False,
    }

    async def execute(self, args, context):
        if args.get("operation") != "write":
            from dataclasses import replace
            from sqlalchemy import text
            from sqlalchemy.ext.asyncio import AsyncSession
            async with AsyncSession(bind=context.db.bind, expire_on_commit=False) as reader:
                await reader.execute(text("BEGIN"))
                return await self._execute(args, replace(context, db=reader))
        return await self._execute(args, context)

    async def _execute(self, args, context):
        if context.agent_id not in self.restricted_to:
            return json.dumps({"status": "error", "detail": "Solus belongs to Sentinel; hand off the original evidence."})
        try:
            operation = args["operation"]
            if operation == "write":
                result = await run_financial_command(context, args["command"])
            elif operation == "books":
                result = await reports.books(context.db, context.user_id)
            else:
                book_id = args["book_id"]
                if operation == "accounts":
                    result = await reports.accounts(context.db, context.user_id, book_id)
                elif operation == "events":
                    result = await reports.events(context.db, context.user_id, book_id, before=args.get("cursor"))
                elif operation == "summary":
                    result = await reports.summary(context.db, context.user_id, book_id, args["period"])
                elif operation == "schedules":
                    result = await reports.schedules(context.db, context.user_id, book_id, period=args.get("period"), after=args.get("cursor"))
                elif operation == "plans":
                    result = await reports.plans(context.db, context.user_id, book_id)
                elif operation == "forecast":
                    result = await reports.forecast(context.db, context.user_id, book_id, args["period"])
                elif operation == "statement":
                    result = await reports.statement(context.db, context.user_id, book_id, args["account_id"], args["period"])
                elif operation == "business":
                    result = await reports.business_report(context.db, context.user_id, book_id, args["period"])
                elif operation == "positions":
                    from app.services.solus.investments import positions
                    result = await positions(context.db, context.user_id, book_id, args["period"])
                elif operation == "reconciliations":
                    result = await reports.reconciliations(context.db, context.user_id, book_id)
                else:
                    raise ValueError("Unknown Solus operation.")
            return json.dumps(result, ensure_ascii=False)
        except (ValueError, KeyError) as exc:
            return json.dumps({"status": "error", "detail": str(exc)}, ensure_ascii=False)
