"""Incremental Orion work. Each source slice and review has its own durable receipt."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from time import monotonic
from uuid import uuid4
from sqlalchemy import select, update, case
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from app.database import write_session
from app.models.iacon import IaconWork, IaconCall, IaconFollowup, IaconSourceRetirement
from app.models.memory_serving import MemoryChange
from app.schemas.iacon import Command
from app.services.iacon_budget import BudgetDenied
from app.services.iacon_catalogue import Conflict


class Orion:
    def __init__(self, catalogue, budget, model_policy, prompts, *, enabled=True):
        self.catalogue, self.sessions = catalogue, catalogue.sessions
        self.budget, self.model_policy, self.prompts = budget, model_policy, prompts
        self.enabled = enabled
        self.lock = asyncio.Lock()
        self.library_indexer = None

    async def enqueue(self, *, user_id, kind, unique_key, payload, db=None, retry=False):
        if db is None:
            async with self.sessions() as own, own.begin():
                return await self.enqueue(user_id=user_id, kind=kind, unique_key=unique_key, payload=payload,
                                          db=own, retry=retry)
        insert = sqlite_insert if db.bind.dialect.name == "sqlite" else pg_insert
        key = kind + ":" + unique_key
        result = await db.execute(insert(IaconWork).values(id=uuid4().hex, user_id=user_id,
            work_key=key, kind=kind, payload=payload, status="pending", attempts=0,
            available_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc)
        ).on_conflict_do_nothing(index_elements=["user_id", "work_key"]))
        if not result.rowcount:
            prior = (await db.execute(select(IaconWork).where(IaconWork.user_id == user_id,
                IaconWork.work_key == key))).scalar_one()
            if prior.payload != payload:
                raise Conflict("Work key was reused for a different payload")
            if retry and kind == "library_index":
                now = datetime.now(timezone.utc)
                changed = await db.execute(update(IaconWork).where(
                    IaconWork.id == prior.id, IaconWork.status.in_(("held", "quarantined", "done")),
                ).values(status="pending", error=None, result=None, claim=None, available_at=now, updated_at=now))
                if changed.rowcount:
                    from app.models.library import LibraryIndexGeneration
                    await db.execute(update(LibraryIndexGeneration).where(
                        LibraryIndexGeneration.id == unique_key,
                        LibraryIndexGeneration.status.not_in(("ready_keyword", "ready_ocr")),
                    ).values(status="pending", progress={"stage": "queued"}, failure_details=None))
                    return key
        return key if result.rowcount else None

    async def intake(self, user_id):
        async with write_session(self.sessions) as db:
            sources = (await db.execute(select(MemoryChange).where(MemoryChange.user_id == user_id,
                MemoryChange.source_role == "owner", MemoryChange.revoked.is_(False),
                MemoryChange.id.not_in(select(IaconSourceRetirement.source_id).where(
                    IaconSourceRetirement.user_id == user_id)),
                MemoryChange.outcome.in_(["pending", "iacon_pending"])
            ).order_by(MemoryChange.id).limit(32))).scalars().all()
            for source in sources:
                start = source.capture_offset
                await self.enqueue(user_id=user_id, kind="interpret", unique_key=f"{source.id}:{start}",
                    payload={"source_id": source.id, "start": start, "end": min(start + 6000, len(source.content))}, db=db)
                source.outcome = "iacon_queued"

    async def propose(self, context, command):
        # Agents propose; only reviewed durable jobs can invoke the canonical writer.
        if command.kind == "document":
            raise ValueError("Use the owner's native document writer")
        await self.enqueue(user_id=context.user_id, kind="review", unique_key="agent:" + command.command_id,
                           payload={"command": command.model_dump(mode="json")})
        return {"status": "pending_review", "command_id": command.command_id}

    async def resolve(self, user_id, job_id, body):
        async with self.sessions() as db, db.begin():
            job = (await db.execute(select(IaconWork).where(IaconWork.id == job_id,
                IaconWork.user_id == user_id))).scalar_one_or_none()
            if not job or job.status != body.expected_status:
                raise Conflict("Work status changed or job not found")
            if body.action == "resume_saved":
                calls = (await db.execute(select(IaconCall).where(IaconCall.user_id == user_id,
                    IaconCall.work_id == job_id))).scalars().all()
                if any(c.status != "complete" or not c.response for c in calls):
                    raise ValueError("Unknown provider outcomes cannot be redispatched; reconcile or discard")
                if job.status == "quarantined":
                    raise ValueError("Invalid contracts require a new corrected proposal, not a retry")
            result = await db.execute(update(IaconWork).where(IaconWork.id == job_id,
                IaconWork.status == body.expected_status).values(
                    status="discarded" if body.action == "discard" else "pending", claim=None,
                    available_at=datetime.now(timezone.utc), error=None))
            if result.rowcount != 1:
                raise Conflict("Work changed during resolution")
            return {"status": "discarded" if body.action == "discard" else "pending"}

    async def drain(self, context, *, limit=None, library_only=False):
        if (not self.enabled and not library_only) or self.lock.locked():
            return {"status": "disabled_or_busy", "processed": 0}
        _, policy = await self.budget.policy(context.user_id)
        if policy.paused:
            return {"status": "paused", "processed": 0}
        outcomes = []
        async with self.lock:
            if not library_only:
                await self.intake(context.user_id)
            scope = (IaconWork.kind == "library_index",) if library_only else ()
            deadline = monotonic() + policy.run_seconds
            for _ in range(min(limit or policy.units_per_run, policy.units_per_run)):
                if monotonic() >= deadline:
                    break
                now = datetime.now(timezone.utc)
                claim = uuid4().hex
                async with self.sessions() as db, db.begin():
                    # Expired leases resume exact saved responses; unknown calls remain held.
                    await db.execute(update(IaconWork).where(IaconWork.user_id == context.user_id,
                        IaconWork.status == "running", IaconWork.available_at < now, *scope
                    ).values(status="pending", claim=None))
                    job = (await db.execute(select(IaconWork).where(IaconWork.user_id == context.user_id,
                        IaconWork.status == "pending", IaconWork.available_at <= now, *scope
                    ).order_by(case((IaconWork.kind.in_(["library_index", "refresh"]), 0), else_=1),
                               IaconWork.available_at, IaconWork.id).limit(1))).scalar_one_or_none()
                    if not job:
                        break
                    taken = await db.execute(update(IaconWork).where(IaconWork.id == job.id,
                        IaconWork.status == "pending").values(status="running", claim=claim,
                        attempts=IaconWork.attempts + 1, available_at=now + timedelta(minutes=5)))
                    if taken.rowcount != 1:
                        continue
                try:
                    async with asyncio.timeout(max(0.001, deadline - monotonic())):
                        result = await self._run(context, job)
                    status, error = "done", None
                except BudgetDenied as exc:
                    # Budget denial is resumable without calls; uncertain dispatch is held.
                    status = "held" if "reconciliation" in str(exc) else "pending"
                    result, error = None, str(exc)
                except (ValueError, KeyError) as exc:
                    status, result, error = "quarantined", None, str(exc)[:256]
                except Exception as exc:
                    status, result, error = "held", None, type(exc).__name__
                async with self.sessions() as db, db.begin():
                    await db.execute(update(IaconWork).where(IaconWork.id == job.id, IaconWork.claim == claim
                    ).values(status=status, result=result, error=error, claim=None,
                             available_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc)))
                    if job.kind != "library_index" and (
                        status in {"held", "quarantined"} or (result or {}).get("status") == "rejected"
                    ):
                        source_ids = ([job.payload["source_id"]] if "source_id" in job.payload else
                                      [e["source_id"] for e in job.payload.get("command", {}).get("evidence", [])])
                        sources = (await db.execute(select(MemoryChange).where(
                            MemoryChange.user_id == context.user_id, MemoryChange.id.in_(source_ids)))).scalars()
                        for source in sources:
                            binding = source.provenance.get("state_binding")
                            if binding:
                                await db.execute(update(IaconFollowup).where(IaconFollowup.user_id == context.user_id,
                                    IaconFollowup.state_id == binding["id"], IaconFollowup.state_version == binding["version"],
                                    IaconFollowup.status == "reply_pending").values(status="needs_review"))
                outcomes.append({"id": job.id, "status": status, "reason": error})
                if status == "pending":
                    break
        return {"processed": len(outcomes), "outcomes": outcomes}

    async def _run(self, context, job):
        if job.kind == "refresh":
            await self.catalogue.refresh(context.user_id, job.payload["record_id"])
            return {"status": "refreshed"}
        if job.kind == "library_index" and self.library_indexer:
            if job.payload.get("source_id") != self.library_indexer.source_id:
                raise ValueError("Library work belongs to another source")
            if job.payload.get("extractor_fingerprint") != self.library_indexer.fingerprint:
                return {"status": "superseded_extractor"}
            await self.library_indexer.index_revision(job.payload["document_id"], job.payload["revision_id"])
            return {"status": "indexed"}
        if job.kind not in {"interpret", "review"}:
            raise ValueError("Unknown maintenance unit; no automatic retries")
        if job.kind == "interpret":
            async with self.sessions() as db:
                source = await db.get(MemoryChange, job.payload["source_id"])
                if not source or source.user_id != context.user_id or source.revoked:
                    raise ValueError("Source withdrawn")
                if await db.get(IaconSourceRetirement, (context.user_id, source.id)):
                    raise ValueError("Source superseded")
            candidates = await self.catalogue.list(context.user_id, limit=30)
            # Bounded catalogue hints; full evidence is fetched for the independent review.
            hints = [{k: r[k] for k in ("id", "version", "kind", "title", "payload")}
                     for r in candidates if r["status"] == "accepted" and r["kind"] != "document"]
            binding = source.provenance.get("state_binding")
            if binding:
                bound = await self.catalogue.get(context.user_id, binding["id"])
                if bound["version"] != binding["version"]:
                    raise Conflict("Late Telegram reply requires review against the newer state")
                hints = [bound]
            else:
                from app.services.memory_runtime import query_terms
                terms = query_terms(source.content[job.payload["start"]:job.payload["end"]])
                from app.services.memory_store import fold
                hints.sort(key=lambda h: -sum(t in fold(json.dumps(h, ensure_ascii=False)) for t in terms))
            data = {"source_id": source.id, "offset": job.payload["start"],
                "source": source.content[job.payload["start"]:job.payload["end"]],
                "prior_context_only": source.provenance.get("prior_context"),
                "state_binding": binding, "existing_records": hints[:8],
                "recorded_at": source.created_at.isoformat(), "timezone": context.timezone}
        else:
            command = Command.model_validate(job.payload["command"])
            if command.kind == "document":
                raise ValueError("Orion cannot rewrite native documents")
            original = await self.catalogue.get(context.user_id, command.record_id) if command.record_id else None
            if original and original["version"] != command.expected_version:
                raise Conflict("Proposal is stale")
            async with self.sessions() as db:
                sources = (await db.execute(select(MemoryChange).where(MemoryChange.user_id == context.user_id,
                    MemoryChange.id.in_([e.source_id for e in command.evidence]), MemoryChange.revoked.is_(False)))).scalars().all()
                retired = await db.scalar(select(IaconSourceRetirement.source_id).where(
                    IaconSourceRetirement.user_id == context.user_id,
                    IaconSourceRetirement.source_id.in_([s.id for s in sources])).limit(1))
            if len({e.source_id for e in command.evidence}) != len(sources):
                raise ValueError("Review evidence is absent or withdrawn")
            if retired is not None:
                raise ValueError("Review evidence has been superseded")
            by_id = {s.id: s for s in sources}
            excerpts = []
            for e in command.evidence:
                s = by_id[e.source_id]
                if s.content[e.start:e.end] != e.quote or e.end > len(s.content) or e.start >= e.end:
                    raise ValueError("Proposal quotes do not match their original evidence")
                binding = s.provenance.get("state_binding")
                if binding and (command.record_id != binding["id"] or command.expected_version != binding["version"]):
                    raise ValueError("Telegram reply targets another state or version")
                excerpts.append({"id": s.id, "role": s.source_role, "text": s.content[max(0, e.start-500):e.end+500],
                                 "state_binding": binding})
            data = {"command": command.model_dump(mode="json"), "existing": original, "evidence": excerpts}
        policy = await self.model_policy.resolve(user_id=context.user_id, agent_id="orion", role="background")
        response = await self.budget.call(context, job.id, policy=policy,
            system=self.prompts(job.kind), messages=[{"role": "user", "content": json.dumps(data, ensure_ascii=False)}],
            max_tokens=4096)
        if response.stop_reason != "end_turn":
            raise ValueError("Truncated response; no automatic repair loop")
        value = json.loads("\n".join(b.text for b in response.content if b.type == "text"))
        if job.kind == "review":
            if not isinstance(value, dict) or set(value) != {"accept", "reason"} or type(value["accept"]) is not bool:
                raise ValueError("Invalid review contract")
            if not value["accept"]:
                return {"status": "rejected", "reason": str(value["reason"])[:500]}
            return await self.catalogue.apply(context.user_id, command, actor="orion_review")
        if not isinstance(value, list) or len(value) > 4:
            raise ValueError("Invalid interpretation contract")
        commands = []
        for index, candidate in enumerate(value):
            command = Command.model_validate({**candidate, "command_id": f"orion:{job.id}:{index}"})
            if any(e.source_id != source.id or e.start < job.payload["start"] or e.end > job.payload["end"]
                   for e in command.evidence):
                raise ValueError("Interpretation evidence is outside this source slice")
            commands.append(command)
        async with self.sessions() as db, db.begin():
            for command in commands:
                await self.enqueue(user_id=context.user_id, kind="review", unique_key=command.command_id,
                                   payload={"command": command.model_dump(mode="json")}, db=db)
            await db.execute(update(MemoryChange).where(MemoryChange.id == source.id,
                MemoryChange.capture_offset == job.payload["start"]).values(capture_offset=job.payload["end"],
                    outcome="iacon_pending" if job.payload["end"] < len(source.content) else "iacon_interpreted"))
        return {"proposals": len(commands)}
