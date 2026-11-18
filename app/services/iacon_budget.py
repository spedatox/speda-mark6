"""Durable, shared, pessimistic reservations before every paid maintenance call."""
import asyncio
from dataclasses import replace
from datetime import datetime
import json
import math
from zoneinfo import ZoneInfo
from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from app.models.iacon import IaconControl, IaconAllowance, IaconCall, IaconMaintenanceRoute
from app.schemas.iacon import MaintenancePolicy
from app.schemas.llm import LLMMessage, Block
from app.services.iacon_catalogue import Conflict


class BudgetDenied(RuntimeError):
    def __init__(self, message, *, code="maintenance_denied"):
        super().__init__(message)
        self.code = code


class MaintenanceBudget:
    def __init__(self, sessions, client):
        self.sessions, self.client = sessions, client

    async def policy(self, user_id):
        async with self.sessions() as db:
            row = await db.get(IaconControl, user_id)
            return (row.version, await self._limits(db, row)) if row else (0, MaintenancePolicy())

    @staticmethod
    async def _limits(db, row):
        route = await db.get(IaconMaintenanceRoute, row.user_id)
        model = route.model if route and route.policy_revision == row.version else None
        return MaintenancePolicy.model_validate({**row.policy, "model": model})

    async def save(self, user_id, expected_version, policy):
        if policy.model:
            from app.services.model_policy import parse_model_ref
            parse_model_ref(policy.model)
            if not policy.paused and policy.model not in policy.prices:
                raise ValueError("The selected maintenance model needs a configured conservative rate")
        async with self.sessions() as db, db.begin():
            insert = sqlite_insert if db.bind.dialect.name == "sqlite" else pg_insert
            stored = policy.model_dump(exclude={"model"})
            if expected_version == 0:
                result = await db.execute(insert(IaconControl).values(user_id=user_id, version=1,
                    policy=stored).on_conflict_do_nothing(index_elements=["user_id"]))
            else:
                result = await db.execute(update(IaconControl).where(IaconControl.user_id == user_id,
                    IaconControl.version == expected_version).values(version=expected_version + 1,
                        policy=stored))
            if result.rowcount != 1:
                raise Conflict("Maintenance policy version conflict")
            if policy.model or await db.get(IaconMaintenanceRoute, user_id):
                route = dict(policy_revision=expected_version + 1, model=policy.model)
                await db.execute(insert(IaconMaintenanceRoute).values(user_id=user_id, **route)
                                 .on_conflict_do_update(index_elements=["user_id"], set_=route))
        return {"version": expected_version + 1, "policy": policy.model_dump()}

    async def reserve(self, context, work_id, policy, tokens):
        if tokens <= 0:
            raise BudgetDenied("Missing token ceiling")
        day = datetime.now(ZoneInfo(context.timezone)).date().isoformat()
        async with self.sessions() as db, db.begin():
            # A write locks the policy row through admission, including on SQLite.
            await db.execute(update(IaconControl).where(IaconControl.user_id == context.user_id
                ).values(version=IaconControl.version))
            control = await db.get(IaconControl, context.user_id)
            prior = (await db.execute(select(IaconCall.id).where(IaconCall.user_id == context.user_id,
                IaconCall.work_id == work_id))).scalar_one_or_none()
            limits = await self._limits(db, control) if control else MaintenancePolicy()
            if prior:
                raise BudgetDenied("Previous provider outcome requires reconciliation", code="reconciling")
            if limits.model and limits.model != policy.spec.ref:
                raise BudgetDenied("Maintenance model changed before dispatch", code="policy_changed")
            price = self.rate(limits, policy.spec.ref)
            cost = math.ceil(tokens * price)
            insert = sqlite_insert if db.bind.dialect.name == "sqlite" else pg_insert
            await db.execute(insert(IaconAllowance).values(user_id=context.user_id, day=day,
                calls=0, tokens=0, microdollars=0).on_conflict_do_nothing(index_elements=["user_id", "day"]))
            accepted = await db.execute(update(IaconAllowance).where(
                IaconAllowance.user_id == context.user_id, IaconAllowance.day == day,
                IaconAllowance.calls + 1 <= limits.daily_calls,
                IaconAllowance.tokens + tokens <= limits.daily_tokens,
                IaconAllowance.microdollars + cost <= limits.daily_microdollars
            ).values(calls=IaconAllowance.calls + 1, tokens=IaconAllowance.tokens + tokens,
                     microdollars=IaconAllowance.microdollars + cost))
            if accepted.rowcount != 1:
                raise BudgetDenied("Daily maintenance budget exhausted", code="daily_limit")
            row = IaconCall(user_id=context.user_id, work_id=work_id, day=day,
                            route={**policy.receipt(), "maintenance_revision": control.version if control else 0},
                            reserved_tokens=tokens, reserved_microdollars=cost)
            db.add(row)
            await db.flush()
            return row.id

    async def call(self, context, work_id, *, policy, system, messages, max_tokens=2048):
        # Only a durably completed response can be replayed. Unknown outcomes never redispatch.
        async with self.sessions() as db:
            prior = (await db.execute(select(IaconCall).where(IaconCall.user_id == context.user_id,
                IaconCall.work_id == work_id).order_by(IaconCall.created_at.desc()).limit(1))).scalar_one_or_none()
            if prior:
                if prior.status == "complete" and prior.response:
                    return LLMMessage(content=[Block(**b) for b in prior.response["content"]],
                        stop_reason=prior.response["stop_reason"], usage=prior.usage)
                raise BudgetDenied("Previous provider outcome requires reconciliation", code="reconciling")
        # UTF-8 bytes are a conservative input-token bound, plus framing allowance.
        tokens = len(json.dumps({"system": system, "messages": messages}, ensure_ascii=False).encode()) + 1024 + max_tokens
        # Some provider protocols account for reasoning separately from visible output.
        tokens += max(0, policy.wire.get("thinkingConfig", {}).get("thinkingBudget", 0))
        policy = replace(policy, fallbacks=())
        from app.services.llm_protocol import request_body
        request_body(policy, system, messages, [], max_tokens)  # Local contract check before admission.
        if hasattr(self.client, "configured") and not self.client.configured(policy.spec.ref):
            raise BudgetDenied(f"Maintenance provider is not configured for {policy.spec.ref}", code="provider_unconfigured")
        call_id = await self.reserve(context, work_id, policy, tokens)
        try:
            async with asyncio.timeout(min(20, policy.remaining())):
                response = await self.client.create_message(model=policy.spec.ref, policy=policy,
                    user_id=context.user_id, agent_id="orion", role="background", system=system,
                    messages=messages, max_tokens=max_tokens)
            async with self.sessions() as db, db.begin():
                await db.execute(update(IaconCall).where(IaconCall.id == call_id).values(status="complete",
                    usage=response.usage, response={"content": [b.model_dump() for b in response.content],
                                                   "stop_reason": response.stop_reason}))
            # Keep reservations at their ceiling: no uncertain/cached-token refunds.
            return response
        except BaseException:
            async with self.sessions() as db, db.begin():
                await db.execute(update(IaconCall).where(IaconCall.id == call_id).values(status="unknown"))
            raise

    @staticmethod
    def rate(limits, model):
        if limits.paused:
            raise BudgetDenied("Maintenance is paused", code="paused")
        price = limits.prices.get(model)
        if price is None or not math.isfinite(price) or price <= 0:
            raise BudgetDenied(f"No conservative maintenance rate configured for {model}", code="missing_rate")
        return price

    async def status(self, user_id):
        version, policy = await self.policy(user_id)
        async with self.sessions() as db:
            days = (await db.execute(select(IaconAllowance).where(IaconAllowance.user_id == user_id)
                .order_by(IaconAllowance.day.desc()).limit(7))).scalars()
            calls = (await db.execute(select(IaconCall).where(IaconCall.user_id == user_id)
                .order_by(IaconCall.created_at.desc()).limit(20))).scalars()
            return {"version": version, "policy": policy.model_dump(), "calls": [
                {"id": c.id, "work_id": c.work_id, "route": c.route, "status": c.status,
                 "usage": c.usage, "reserved_tokens": c.reserved_tokens,
                 "reserved_microdollars": c.reserved_microdollars} for c in calls], "reservations": [
                {"day": d.day, "calls": d.calls, "token_ceiling": d.tokens,
                 "microdollar_ceiling": d.microdollars} for d in days]}
