"""One persistent reservation tree for room participants and their workers."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import math
import time
from uuid import uuid4
from app.database import write_session
from sqlalchemy import func, select, update, or_, and_
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from app.models.coordination import CoordinationControl, CoordinationDay, CoordinationRun, ModelCallReceipt
from app.schemas.coordination import SpendingPolicy
from app.schemas.llm import ProviderError, LLMMessage, Block
from app.services.llm_protocol import request_body


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


class CoordinationError(ProviderError):
    def __init__(self, code, message, status=409):
        super().__init__(code, message)
        self.status = status


def insert_for(db, model):
    return (sqlite_insert if db.bind.dialect.name == "sqlite" else pg_insert)(model)


def reported_chat_tokens(policy, usage):
    """Accept complete chat usage; leave unknown or partial reports reserved."""
    if policy.spec.protocol != "chat" or not isinstance(usage, dict):
        return None
    prompt, output, total = (usage.get(key) for key in ("prompt_tokens", "completion_tokens", "total_tokens"))
    if (all(type(value) is int for value in (prompt, output, total)) and
            prompt > 0 and output >= 0 and total == prompt + output):
        return min(total, 20_000_001)  # Any larger report already exceeds every supported run limit.
    return None


class ReplayStream:
    def __init__(self, payload, policy):
        self.final = LLMMessage(content=[Block(**b) for b in payload["content"]],
            stop_reason=payload["stop_reason"], usage=payload["usage"],
            continuation=payload.get("continuation"), policy=policy)

    async def event_stream(self):
        for block in self.final.content:
            if block.type == "text":
                yield "text", block.text

    async def get_final_message(self):
        return self.final


class CoordinationBudget:
    def __init__(self, sessions, client):
        self.sessions, self.client = sessions, client
        self._capacity = asyncio.Condition()

    async def reserve_when_available(self, context, key, policy, body, tokens):
        """Wait only for known in-flight admission slots, never repeat a send."""
        while True:
            await context.coordination.ensure_active(context)
            try:
                return await self.reserve(context,key,policy,body,tokens)
            except CoordinationError as exc:
                if exc.code not in {'concurrency','owner_concurrency'}:
                    raise
                async with self.sessions() as db:
                    query=select(func.count()).select_from(ModelCallReceipt).join(CoordinationRun,
                        CoordinationRun.id == ModelCallReceipt.run_id).where(
                        CoordinationRun.user_id == context.user_id,or_(ModelCallReceipt.status == 'unknown',
                            and_(ModelCallReceipt.status == 'started',or_(
                                CoordinationRun.status.not_in(['running','waiting_tasks']),
                                CoordinationRun.lease_until <= time.time(),CoordinationRun.deadline <= time.time()))))
                    if exc.code == 'concurrency':query=query.where(ModelCallReceipt.run_id == context.run_id)
                    if await db.scalar(query):
                        raise CoordinationError('outcome_unknown','An uncertain provider outcome occupies capacity; reconcile before more calls') from None
                remaining=policy.deadline-time.monotonic()
                if remaining <= 0:
                    raise CoordinationError('deadline','The original worker/model admission deadline expired') from None
                try:
                    async with self._capacity:
                        await asyncio.wait_for(self._capacity.wait(),min(.2,remaining))
                except TimeoutError:
                    pass

    async def policy(self, owner):
        async with self.sessions() as db:
            row = await db.get(CoordinationControl, owner)
            return (row.version, SpendingPolicy.model_validate(row.policy)) if row else (0, SpendingPolicy())

    async def save(self, owner, version, policy):
        async with write_session(self.sessions) as db:
            if version == 0:
                result = await db.execute(insert_for(db, CoordinationControl).values(
                    user_id=owner, version=1, policy=policy.model_dump()).on_conflict_do_nothing())
            else:
                result = await db.execute(update(CoordinationControl).where(
                    CoordinationControl.user_id == owner, CoordinationControl.version == version
                ).values(version=version + 1, policy=policy.model_dump()))
            if result.rowcount != 1:
                raise CoordinationError("version", "Spending policy changed; reread before editing")
        return {"version": version + 1, "policy": policy.model_dump()}

    async def reserve(self, context, key, policy, body, token_ceiling):
        day = datetime.now(timezone.utc).date().isoformat()
        async with write_session(self.sessions) as db:
            # All paid admissions acquire this owner row first. Pause and concurrent
            # roots serialize here; no read-then-write allowance race.
            await db.execute(update(CoordinationControl).where(CoordinationControl.user_id == context.user_id)
                             .values(version=CoordinationControl.version))
            control = await db.get(CoordinationControl, context.user_id)
            run = (await db.execute(select(CoordinationRun).where(CoordinationRun.id == context.run_id,
                CoordinationRun.user_id == context.user_id))).scalar_one_or_none()
            if run is None or run.cancelled or run.status not in {"running", "waiting_tasks"} or run.deadline <= time.time():
                raise CoordinationError("inactive_run", "The run is stopped, expired or awaiting reconciliation")
            if run.executor != context.coordination.identity or run.lease_until <= time.time():
                raise CoordinationError("executor_fenced", "The model-call executor no longer owns this run")
            prior = (await db.execute(select(ModelCallReceipt).where(
                ModelCallReceipt.run_id == run.id, ModelCallReceipt.call_key == key))).scalar_one_or_none()
            request_hash = digest({"body": body, "route": policy.receipt()})
            if prior:
                if prior.request_hash != request_hash:
                    raise CoordinationError("replay_conflict", "Saved provider call differs from this execution")
                if prior.status != "complete":
                    raise CoordinationError("call_unknown", "A prior paid call requires reconciliation; it will not be resent")
                return prior.id, prior.response
            live = SpendingPolicy.model_validate(control.policy) if control else SpendingPolicy()
            fixed = SpendingPolicy.model_validate(run.limits)
            if live.paused:
                raise CoordinationError("budget_paused", "Coordination spending is paused")
            price = fixed.prices.get(policy.spec.ref)
            if fixed.enforce_limits and price is None:
                raise CoordinationError("unpriced_route", "This model has no verified conservative spending rate")
            from app.services.llm_protocol import count_native_images
            images = count_native_images(body)
            if fixed.enforce_limits and images and not price.image_token_ceiling:
                raise CoordinationError('image_budget', 'Configure the model per-image token ceiling before image calls', 422)
            if fixed.enforce_limits and policy.spec.protocol == "google" and policy.requested.mode != "off" and not price.extra_output_ceiling:
                raise CoordinationError("reasoning_ceiling", "This route needs a separate reasoning-token ceiling")
            tokens = token_ceiling
            if price:
                tokens += price.extra_output_ceiling + images * price.image_token_ceiling
            cost = math.ceil(tokens * price.microdollars_per_token) if price else 0
            call_cap_exceeded = (
                not run.policy.get("shared_call_cap_exempt", False) and run.calls + 1 > fixed.run.calls
            )
            if call_cap_exceeded:
                raise CoordinationError("run_budget", f"The run reached its {fixed.run.calls:,}-call model limit.")
            if run.tokens + tokens > fixed.run.tokens:
                remaining = max(0, fixed.run.tokens - run.tokens)
                raise CoordinationError("run_budget",
                    f"The next model call needs {tokens:,} reserved tokens. The run has {remaining:,} of "
                    f"{fixed.run.tokens:,} tokens left.")
            if fixed.enforce_limits and run.microdollars + cost > fixed.run.microdollars:
                raise CoordinationError("run_budget", "The next model call exceeds the run spending limit.")
            active = await db.scalar(select(func.count()).select_from(ModelCallReceipt).where(
                ModelCallReceipt.run_id == run.id, ModelCallReceipt.status.in_(["started", "unknown"])))
            if active >= fixed.run.concurrency:
                raise CoordinationError("concurrency", "The shared model-call slots are occupied")
            owner_active = await db.scalar(select(func.count()).select_from(ModelCallReceipt)
                .join(CoordinationRun, CoordinationRun.id == ModelCallReceipt.run_id).where(
                    CoordinationRun.user_id == context.user_id,
                    ModelCallReceipt.status.in_(["started", "unknown"])))
            if owner_active >= live.concurrent_calls:
                raise CoordinationError("owner_concurrency", "The owner's model-call slots are occupied or awaiting reconciliation")
            await db.execute(insert_for(db, CoordinationDay).values(user_id=context.user_id, day=day,
                calls=0, tokens=0, microdollars=0).on_conflict_do_nothing())
            allowance = update(CoordinationDay).where(
                CoordinationDay.user_id == context.user_id, CoordinationDay.day == day)
            if live.enforce_limits:
                allowance = allowance.where(CoordinationDay.calls + 1 <= live.daily_calls,
                    CoordinationDay.tokens + tokens <= live.daily_tokens,
                    CoordinationDay.microdollars + cost <= live.daily_microdollars)
            admitted = await db.execute(allowance.values(calls=CoordinationDay.calls + 1,
                tokens=CoordinationDay.tokens + tokens, microdollars=CoordinationDay.microdollars + cost))
            if admitted.rowcount != 1:
                raise CoordinationError("daily_budget", "The owner's daily coordination allowance is exhausted")
            run.calls += 1
            run.tokens += tokens
            run.microdollars += cost
            receipt = ModelCallReceipt(id=uuid4().hex, run_id=run.id, call_key=key,
                request_hash=request_hash, route=policy.receipt(), tokens=tokens, microdollars=cost)
            db.add(receipt)
            await db.flush()
            return receipt.id, None

    @asynccontextmanager
    async def stream(self, context, **args):
        await context.coordination.ensure_active(context)
        policy = replace(args["policy"], fallbacks=())
        args["policy"] = policy
        body = request_body(policy, args["system"], args["messages"], args.get("tools", []), args["max_tokens"])
        if hasattr(self.client, "configured") and not self.client.configured(policy.spec.ref):
            raise CoordinationError("not_configured", "The selected provider is not configured", 503)
        # Byte bound plus framing, output and any route-specific reasoning ceiling.
        from app.services.llm_protocol import budget_payload
        tokens = len(json.dumps(budget_payload(body), ensure_ascii=False).encode()) + 2048 + args["max_tokens"]
        key = f"{context.step_id or context.request_id}:{context.model_call_index}"
        context.model_call_index += 1
        receipt_id, saved = await self.reserve_when_available(context, key, policy, body, tokens)
        if saved is not None:
            yield ReplayStream(saved, policy)
            return
        try:
            async with self.client.stream_message(**args) as stream:
                yield stream
                result = await stream.get_final_message()
            payload = {"content": [b.model_dump() for b in result.content],
                "stop_reason": result.stop_reason, "usage": result.usage,
                "continuation": getattr(result, "continuation", None)}
            actual_tokens = reported_chat_tokens(policy, result.usage)
            async with write_session(self.sessions) as db:
                run = await db.get(CoordinationRun, context.run_id) if actual_tokens is not None else None
                settle = run is not None and run.policy.get("shared_call_cap_exempt", False)
                if settle:
                    # Serialize token settlement with new admissions. Daily and monetary
                    # reservations retain their original conservative ceilings.
                    await db.execute(update(CoordinationControl).where(CoordinationControl.user_id == context.user_id)
                                     .values(version=CoordinationControl.version))
                reserved = await db.scalar(update(ModelCallReceipt).where(ModelCallReceipt.id == receipt_id,
                    ModelCallReceipt.status != "complete").values(status="complete", response=payload, usage=result.usage)
                    .returning(ModelCallReceipt.tokens))
                if settle and reserved is not None:
                    await db.execute(update(CoordinationRun).where(CoordinationRun.id == context.run_id)
                                     .values(tokens=CoordinationRun.tokens + actual_tokens - reserved))
            async with self._capacity:
                self._capacity.notify_all()
        except BaseException:
            async def hold():
                async with write_session(self.sessions) as db:
                    await db.execute(update(ModelCallReceipt).where(ModelCallReceipt.id == receipt_id,
                        ModelCallReceipt.status == "started").values(status="unknown"))
            await asyncio.shield(hold())
            async with self._capacity:
                self._capacity.notify_all()
            raise

    async def status(self, owner):
        version, policy = await self.policy(owner)
        async with self.sessions() as db:
            days = (await db.execute(select(CoordinationDay).where(CoordinationDay.user_id == owner)
                .order_by(CoordinationDay.day.desc()).limit(7))).scalars().all()
        return {"version": version, "policy": policy.model_dump(), "reserved": [
            {"day": d.day, "calls": d.calls, "tokens": d.tokens, "microdollars": d.microdollars} for d in days]}
