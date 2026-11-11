"""Owner-wide paid-attempt ceiling shared by every application LLM route."""
from datetime import datetime, timezone
import json
import math
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import uuid4
from app.database import write_session
from sqlalchemy import select, update, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import JSON, String, ForeignKey
from pydantic import Field
from app.database import Base
from app.schemas.coordination import Contract, Price
from app.services.coordination_budget import CoordinationError, insert_for


class GlobalPolicy(Contract):
    paused: bool = True
    enforce_limits: bool = Field(default=True, description="Enforce spending allowances and require configured prices. Disable to track calls without spending admission limits.")
    daily_calls: int = Field(default=0, ge=0)
    daily_tokens: int = Field(default=0, ge=0)
    daily_microdollars: int = Field(default=0, ge=0)
    lifetime_calls: int | None = Field(default=None, ge=0)
    lifetime_tokens: int | None = Field(default=None, ge=0)
    lifetime_microdollars: int | None = Field(default=None, ge=0)
    concurrent_calls: int = Field(default=8, ge=1, le=32)
    prices: dict[str, Price] = Field(default_factory=dict)
    # Speech uses explicit resource units, never fictitious model tokens.
    external_prices: dict[str, Annotated[float,Field(gt=0,allow_inf_nan=False)]] = Field(default_factory=dict)


class GlobalUpdate(Contract):
    expected_version: int = Field(ge=0)
    policy: GlobalPolicy


class GlobalControl(Base):
    __tablename__ = "global_spending_control"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    version: Mapped[int] = mapped_column()
    policy: Mapped[dict] = mapped_column(JSON)


class GlobalDay(Base):
    __tablename__ = "global_spending_days"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    calls: Mapped[int] = mapped_column(default=0)
    tokens: Mapped[int] = mapped_column(default=0)
    microdollars: Mapped[int] = mapped_column(default=0)


class GlobalCall(Base):
    __tablename__ = "global_spending_calls"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    day: Mapped[str] = mapped_column(String(10))
    route: Mapped[str] = mapped_column(String(255))
    purpose: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(24), default="started")
    tokens: Mapped[int] = mapped_column()
    microdollars: Mapped[int] = mapped_column()


class GlobalBudget:
    def __init__(self, sessions):
        self.sessions = sessions
        self.lifecycle = None

    @asynccontextmanager
    async def admission(self):
        if self.lifecycle is None:
            yield
            return
        async with self.lifecycle.lifecycle_lock:
            if self.lifecycle.restarting or self.lifecycle.closing:
                raise CoordinationError('backend_restarting', 'Backend is restarting; no paid call was admitted.', 503)
            yield

    async def status(self, owner):
        async with self.sessions() as db:
            control = await db.get(GlobalControl, owner)
            days = (await db.execute(select(GlobalDay).where(GlobalDay.user_id == owner)
                .order_by(GlobalDay.day.desc()).limit(7))).scalars()
            total = (await db.execute(select(func.count(), func.coalesce(func.sum(GlobalCall.tokens), 0),
                func.coalesce(func.sum(GlobalCall.microdollars), 0)).where(GlobalCall.user_id == owner))).one()
            return {"version": control.version if control else 0,
                "policy": control.policy if control else GlobalPolicy().model_dump(),
                "lifetime_reserved": dict(zip(("calls", "tokens", "microdollars"), total)),
                "reserved": [{"day": d.day, "calls": d.calls, "tokens": d.tokens,
                    "microdollars": d.microdollars} for d in days]}

    async def save(self, owner, version, policy):
        async with write_session(self.sessions) as db:
            if version == 0:
                result = await db.execute(insert_for(db, GlobalControl).values(user_id=owner,
                    version=1, policy=policy.model_dump()).on_conflict_do_nothing())
            else:
                result = await db.execute(update(GlobalControl).where(GlobalControl.user_id == owner,
                    GlobalControl.version == version).values(version=version+1, policy=policy.model_dump()))
            if result.rowcount != 1:
                raise CoordinationError("global_budget_version", "Global spending policy changed")
        return await self.status(owner)

    async def preflight(self, owner, policy, *, images=False, db=None):
        """Reject unavailable spending before chat/automation admissions; not a reservation."""
        if db is None:
            async with self.sessions() as own:
                return await self.preflight(owner, policy, images=images, db=own)
        row = await db.get(GlobalControl, owner)
        limits = GlobalPolicy.model_validate(row.policy) if row else GlobalPolicy()
        self._price(limits, policy, images)

    @staticmethod
    def _price(limits, policy, images=False):
        price = limits.prices.get(policy.spec.ref)
        if limits.paused or (limits.enforce_limits and not price):
            raise CoordinationError("global_budget_paused", "Global spending is paused or this model is unpriced")
        if not limits.enforce_limits:
            return price
        if policy.spec.protocol == "google" and policy.requested.mode != "off" and not price.extra_output_ceiling:
            raise CoordinationError("reasoning_ceiling", "Set a global reasoning-token ceiling for this model")
        if images and not price.image_token_ceiling:
            raise CoordinationError('image_budget', 'Configure the global per-image token ceiling before image calls', 422)
        return price

    async def reserve(self, owner, policy, body, max_tokens, purpose):
        async with self.admission():
            return await self._reserve(owner, policy, body, max_tokens, purpose)

    async def _reserve(self, owner, policy, body, max_tokens, purpose):
        day = datetime.now(timezone.utc).date().isoformat()
        async with write_session(self.sessions) as db:
            await db.execute(update(GlobalControl).where(GlobalControl.user_id == owner)
                .values(version=GlobalControl.version))
            row = await db.get(GlobalControl, owner)
            limits = GlobalPolicy.model_validate(row.policy) if row else GlobalPolicy()
            from app.services.llm_protocol import count_native_images, budget_payload
            images = count_native_images(body)
            price = self._price(limits, policy, images)
            tokens = len(json.dumps(budget_payload(body), ensure_ascii=False).encode()) + 2048 + max_tokens
            if price:
                tokens += price.extra_output_ceiling + images * price.image_token_ceiling
            # An unpriced call has no monetary reservation; this is not its billed cost.
            cost = math.ceil(tokens * price.microdollars_per_token) if price else 0
            # Closed streams keep their spending receipts, including unknown outcomes.
            live = await db.scalar(select(func.count()).select_from(GlobalCall).where(
                GlobalCall.user_id == owner, GlobalCall.status == "started"))
            if live >= limits.concurrent_calls:
                raise CoordinationError("global_concurrency", "Global call slots are occupied")
            if limits.enforce_limits and any(getattr(limits, name) is not None for name in (
                    "lifetime_calls", "lifetime_tokens", "lifetime_microdollars")):
                total = (await db.execute(select(func.count(), func.coalesce(func.sum(GlobalCall.tokens), 0),
                    func.coalesce(func.sum(GlobalCall.microdollars), 0)).where(GlobalCall.user_id == owner))).one()
                if any(cap is not None and used + delta > cap for cap, used, delta in zip(
                        (limits.lifetime_calls, limits.lifetime_tokens, limits.lifetime_microdollars),
                        total, (1, tokens, cost))):
                    raise CoordinationError("global_lifetime_budget_exhausted", "Global total spending allowance exhausted")
            await db.execute(insert_for(db, GlobalDay).values(user_id=owner, day=day,
                calls=0, tokens=0, microdollars=0).on_conflict_do_nothing())
            allowance = update(GlobalDay).where(GlobalDay.user_id == owner, GlobalDay.day == day)
            if limits.enforce_limits:
                allowance = allowance.where(GlobalDay.calls + 1 <= limits.daily_calls,
                    GlobalDay.tokens + tokens <= limits.daily_tokens,
                    GlobalDay.microdollars + cost <= limits.daily_microdollars)
            accepted = await db.execute(allowance.values(calls=GlobalDay.calls+1,
                tokens=GlobalDay.tokens+tokens, microdollars=GlobalDay.microdollars+cost))
            if accepted.rowcount != 1:
                raise CoordinationError("global_budget_exhausted", "Global daily spending allowance exhausted")
            key = uuid4().hex
            db.add(GlobalCall(id=key, user_id=owner, day=day, route=policy.spec.ref,
                purpose=purpose[:128], tokens=tokens, microdollars=cost))
        return key

    async def finish(self, key, status):
        async with write_session(self.sessions) as db:
            await db.execute(update(GlobalCall).where(GlobalCall.id == key, GlobalCall.status == "started")
                .values(status=status))

    async def reserve_external(self,owner,route,units,purpose):
        async with self.admission():
            return await self._reserve_external(owner,route,units,purpose)

    async def _reserve_external(self,owner,route,units,purpose):
        """Conservative per-resource reservation against the same owner ceiling."""
        if type(units) is not int or units<=0: raise CoordinationError('external_units','A positive bounded resource quantity is required',422)
        day=datetime.now(timezone.utc).date().isoformat()
        async with write_session(self.sessions) as db:
            await db.execute(update(GlobalControl).where(GlobalControl.user_id==owner).values(version=GlobalControl.version))
            row=await db.get(GlobalControl,owner)
            limits=GlobalPolicy.model_validate(row.policy) if row else GlobalPolicy()
            rate=limits.external_prices.get(route,0)
            if limits.paused or (limits.enforce_limits and (not math.isfinite(rate) or rate<=0)):
                raise CoordinationError('global_budget_paused','Paid transport is paused or this resource has no explicit price ceiling')
            cost=math.ceil(units*rate)
            live=await db.scalar(select(func.count()).select_from(GlobalCall).where(
                GlobalCall.user_id==owner, GlobalCall.status=='started'))
            if live>=limits.concurrent_calls: raise CoordinationError('global_concurrency','Paid call slots are occupied')
            total=(await db.execute(select(func.count(),func.coalesce(func.sum(GlobalCall.tokens),0),func.coalesce(func.sum(GlobalCall.microdollars),0)).where(GlobalCall.user_id==owner))).one()
            if limits.enforce_limits and any(cap is not None and used+delta>cap for cap,used,delta in zip(
                (limits.lifetime_calls,limits.lifetime_tokens,limits.lifetime_microdollars),total,(1,0,cost))):
                raise CoordinationError('global_lifetime_budget_exhausted','Global total spending allowance exhausted')
            await db.execute(insert_for(db,GlobalDay).values(user_id=owner,day=day,calls=0,tokens=0,microdollars=0).on_conflict_do_nothing())
            allowance=update(GlobalDay).where(GlobalDay.user_id==owner,GlobalDay.day==day)
            if limits.enforce_limits:
                allowance=allowance.where(GlobalDay.calls+1<=limits.daily_calls,
                    GlobalDay.microdollars+cost<=limits.daily_microdollars)
            changed=await db.execute(allowance.values(calls=GlobalDay.calls+1,microdollars=GlobalDay.microdollars+cost))
            if changed.rowcount!=1: raise CoordinationError('global_budget_exhausted','Global daily spending allowance exhausted')
            identity=uuid4().hex
            db.add(GlobalCall(id=identity,user_id=owner,day=day,route=route,purpose=purpose[:128],tokens=0,microdollars=cost))
        return identity
