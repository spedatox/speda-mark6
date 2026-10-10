"""Durable background-worker reports; never restart a completed worker.

Codex reference: agent/control/{completion,mailbox,delivery}.rs separates
captured outcomes, queue acceptance and delivery. Its completion callback is
best effort. Igor additionally keeps the receipt in its operational database.
This is a bounded drain invoked by completion, turn settlement, startup or n8n,
not a scheduler. A report that may have run tools is never blindly replayed.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from datetime import datetime, timezone

from sqlalchemy import select, update

from app.models.message import Message
from app.models.session import Session
from app.models.worker_execution import WorkerCompletion, WorkerExecution
from app.services.chat_history import final_answer_text
from app.services.workspaces import WorkspaceError

logger = logging.getLogger(__name__)


class CompletionRecoveryService:
    def __init__(self, factory):
        self._factory = factory
        self._deps = None
        self._lock = asyncio.Lock()
        self._closing = False

    def wire(self, **dependencies):
        self._deps = dependencies

    def close(self):
        self._closing = True

    @staticmethod
    def _payload(worker):
        return {"type": "legion_report", "job": f"{worker.worker} report",
                "worker": worker.worker, "task": worker.task, "result": worker.result or "",
                "status": {"completed": "ok", "failed": "error", "interrupted": "cancelled"}.get(worker.status, "unknown"),
                "ticket": worker.ticket_id, "execution_id": worker.id,
                "backend": worker.backend,
                "workspace": worker.workspace, "workshop_project_id": worker.workshop_project_id,
                "resumed": True}

    @staticmethod
    def _valid_session(session, worker):
        return (session is not None and session.user_id == worker.user_id
                and session.agent_id == worker.agent_id and session.ended_at is None)

    async def _origin(self, db, execution_id):
        worker = await db.get(WorkerExecution, execution_id)
        if worker is None or not worker.background or worker.status == "running":
            raise WorkspaceError("No saved background completion", status_code=404)
        session = await db.get(Session, worker.session_id, populate_existing=True)
        if not self._valid_session(session, worker):
            raise WorkspaceError("Completion origin chat is missing, closed or belongs to another agent/owner", status_code=404)
        return worker, session

    async def save_seed(self, db, execution_id, session_manager, session_id, content):
        # Lock/CAS before history writes. Concurrent drains cannot insert two
        # seeds, and seed + admission receipt commit as one transaction.
        claimed = (await db.execute(update(WorkerCompletion).where(
            WorkerCompletion.execution_id == execution_id, WorkerCompletion.status == "pending")
            .values(status="ready", attempts=WorkerCompletion.attempts + 1, last_error=None)
            .returning(WorkerCompletion.execution_id))).scalar_one_or_none()
        if claimed is None:
            raise WorkspaceError("Completion already admitted")
        worker, _ = await self._origin(db, execution_id)
        if worker.session_id != session_id:
            raise WorkspaceError("Completion cannot move to a different chat")
        receipt = await db.get(WorkerCompletion, execution_id, populate_existing=True)
        if receipt.seed_message_id is None:
            content = [*content, {"type": "_speda_meta", "worker_completion": {
                "execution_id": execution_id, "request_id": receipt.request_id}}]
            seed = await session_manager.save_message(db, session_id, "user", content, commit=False)
            receipt.seed_message_id = seed.id
        else:
            seed = await db.get(Message, receipt.seed_message_id)
            if not self._valid_seed(seed, worker, receipt):
                raise WorkspaceError("Saved completion seed is missing or changed")
        await db.commit()

    @staticmethod
    def _valid_seed(seed, worker, receipt):
        return (seed is not None and seed.session_id == worker.session_id and seed.role == "user"
                and isinstance(seed.content, list) and any(
                    isinstance(block, dict) and block.get("type") == "_speda_meta"
                    and block.get("worker_completion") == {"execution_id": worker.id, "request_id": receipt.request_id}
                    for block in seed.content))

    async def run_report(self, execution_id, context, engine_factory):
        # A committed running marker precedes constructing/advancing the engine.
        # After this point a restart cannot prove that tool side effects did not
        # happen. Only a saved terminal assistant message can settle that doubt.
        async with self._factory() as db:
            await self._origin(db, execution_id)
            admitted = (await db.execute(update(WorkerCompletion).where(
                WorkerCompletion.execution_id == execution_id, WorkerCompletion.status == "ready")
                .values(status="running").returning(WorkerCompletion.execution_id))).scalar_one_or_none()
            if admitted is None:
                raise WorkspaceError("Completion report was already started")
            await db.commit()
        async with AsyncExitStack() as lifetime:
            source = engine_factory(context)
            if getattr(source, "aclose", None) is not None:
                lifetime.push_async_callback(source.aclose)
            async for event in source:
                yield event

    async def _proof(self, db, worker, receipt):
        seed = await db.get(Message, receipt.seed_message_id) if receipt.seed_message_id else None
        if not self._valid_seed(seed, worker, receipt):
            return None
        candidates = await db.stream_scalars(select(Message).where(
            Message.session_id == worker.session_id, Message.role == "assistant", Message.id > seed.id)
            .order_by(Message.id))
        try:
            async for message in candidates:
                if not isinstance(message.content, list):
                    continue
                for block in message.content:
                    if not isinstance(block, dict) or block.get("type") != "_speda_meta":
                        continue
                    turn = block.get("turn") or {}
                    if turn.get("request_id") == receipt.request_id and turn.get("status") in {"completed", "failed", "interrupted"}:
                        return message, turn["status"]
        finally:
            await candidates.close()
        return None

    async def settle(self, execution_id):
        async with self._factory() as db:
            receipt = await db.get(WorkerCompletion, execution_id)
            worker = await db.get(WorkerExecution, execution_id)
            proof = await self._proof(db, worker, receipt)
            if proof is None:
                receipt.status = "unknown"
                receipt.last_error = "Report completion is unconfirmed; automatic replay could repeat tool side effects."
            else:
                message, receipt.status = proof
                receipt.response_message_id = message.id
                receipt.last_error = None
            await db.commit()

    async def _recover_rows(self, *, startup=False):
        # No timer/lease stealing. Startup assumes this app is the sole report
        # executor. Live work is never reconciled by elapsed time.
        async with self._factory() as db:
            rows = (await db.execute(select(WorkerCompletion).where(
                WorkerCompletion.status.in_(["ready", "running"])))).scalars().all()
            for receipt in rows:
                if not startup and self._deps["turns"].is_live(receipt.request_id):
                    continue
                worker = await db.get(WorkerExecution, receipt.execution_id)
                proof = await self._proof(db, worker, receipt)
                if proof is not None:
                    message, receipt.status = proof
                    receipt.response_message_id = message.id
                    receipt.last_error = None
                elif receipt.status == "ready":
                    receipt.status = "pending"  # provider-start marker was never committed
                else:
                    receipt.status = "unknown"
                    receipt.last_error = "Report executor disappeared after starting; inspect saved evidence before any follow-up."
            if startup:
                await db.execute(update(WorkerCompletion).where(WorkerCompletion.delivery_status == "sending")
                                 .values(delivery_status="pending", last_error="Push acknowledgement lost; delivery retry may repeat the notification."))
            await db.commit()

    async def recover_on_startup(self):
        async with self._lock:
            await self._recover_rows(startup=True)
        return await self.drain()

    async def _error(self, execution_id, error, *, blocked=False):
        async with self._factory() as db:
            await db.execute(update(WorkerCompletion).where(
                WorkerCompletion.execution_id == execution_id,
                WorkerCompletion.status.in_(["pending", "ready"]))
                .values(status="blocked" if blocked else "pending", last_error=str(error)))
            await db.commit()

    async def _start(self, execution_id):
        from app.core.trigger_runner import start_trigger_turn

        deps = self._deps
        async with self._factory() as db:
            worker, _ = await self._origin(db, execution_id)
            receipt = await db.get(WorkerCompletion, execution_id)
            profile = deps["profiles"].get(worker.agent_id)
            if profile is None:
                raise WorkspaceError("Completion agent profile is unavailable", status_code=404)
            started, _ = await start_trigger_turn(
                db=db, profile=profile, payload=self._payload(worker), output_mode="push",
                request_id=receipt.request_id, user_id=worker.user_id,
                triggered_by="agent", session_id=worker.session_id,
                completion_recovery=self, completion_id=worker.id,
                **{key: value for key, value in deps.items() if key != "profiles"})
            return started

    async def _deliver(self, execution_id):
        try:
            return await self._send_report(execution_id)
        except asyncio.CancelledError:
            # An HTTP drain can be disconnected too. Own and join its final
            # receipt update, even under repeated cancellation; the next drain
            # may retry delivery without needing a process restart.
            settlement = asyncio.create_task(self._delivery_interrupted(execution_id))
            while not settlement.done():
                try:
                    await asyncio.shield(settlement)
                except asyncio.CancelledError:
                    continue
            settlement.result()
            raise

    async def _delivery_interrupted(self, execution_id):
        async with self._factory() as db:
            await db.execute(update(WorkerCompletion).where(
                WorkerCompletion.execution_id == execution_id, WorkerCompletion.delivery_status == "sending")
                .values(delivery_status="pending", last_error="Push delivery was interrupted; retry may repeat an accepted notification."))
            await db.commit()

    async def _send_report(self, execution_id):
        from app.core.trigger_runner import _deliver

        async with self._factory() as db:
            claimed = (await db.execute(update(WorkerCompletion).where(
                WorkerCompletion.execution_id == execution_id, WorkerCompletion.delivery_status == "pending",
                WorkerCompletion.status.in_(["completed", "failed", "interrupted"]))
                .values(delivery_status="sending", delivery_attempts=WorkerCompletion.delivery_attempts + 1)
                .returning(WorkerCompletion.execution_id))).scalar_one_or_none()
            if claimed is None:
                return False
            worker, _ = await self._origin(db, execution_id)
            receipt = await db.get(WorkerCompletion, execution_id, populate_existing=True)
            proof = await self._proof(db, worker, receipt)
            if proof is None or proof[0].id != receipt.response_message_id:
                raise WorkspaceError("Saved report response is missing or changed")
            message, status = proof
            # Use this report's exact response, never the chat's latest answer.
            text = final_answer_text(message.content) or (
                "The report turn returned no closing answer. Saved worker result:\n" + (worker.result or "(empty result)"))
            payload = self._payload(worker)
            request_id = receipt.request_id
            await db.commit()
        profile = self._deps["profiles"].get(worker.agent_id)
        if profile is None:
            raise WorkspaceError("Completion agent profile is unavailable")
        await _deliver(agent_id=worker.agent_id, session_id=worker.session_id,
            request_id=request_id, user_id=worker.user_id, output_mode="push", payload=payload,
            telegram_bots=self._deps["telegram_bots"], session_manager=self._deps["session_manager"],
            status={"completed": "ok", "interrupted": "cancelled"}.get(status, "failed"),
            profile=profile, sanitize_model=profile.background_model(profile.allocate_model("agent")),
            text_override=text, completion_id=execution_id)
        async with self._factory() as db:
            await db.execute(update(WorkerCompletion).where(WorkerCompletion.execution_id == execution_id)
                             .values(delivery_status="delivered", last_error=None))
            await db.commit()
        return True

    async def drain(self, *, limit=32):
        if self._closing or self._deps is None:
            return {"started": 0, "delivered": 0, "deferred": 0, "errors": 0}
        result = {"started": 0, "delivered": 0, "deferred": 0, "errors": 0}
        async with self._lock:
            if self._closing:
                return result
            await self._recover_rows()
            async with self._factory() as db:
                pending = (await db.execute(select(WorkerCompletion.execution_id).where(
                    WorkerCompletion.status == "pending").order_by(WorkerCompletion.updated_at, WorkerCompletion.execution_id)
                    .limit(max(1, min(limit, 100))))).scalars().all()
            for execution_id in pending:
                if self._closing:
                    break
                try:
                    if await self._start(execution_id):
                        result["started"] += 1
                    else:
                        result["deferred"] += 1
                        async with self._factory() as db:
                            await db.execute(update(WorkerCompletion).where(WorkerCompletion.execution_id == execution_id)
                                .values(updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
                                        last_error="Origin chat is busy, report ID is retained, or turn capacity is full; receipt remains queued."))
                            await db.commit()
                except Exception as exc:
                    result["errors"] += 1
                    await self._error(execution_id, exc, blocked=isinstance(exc, WorkspaceError))
                    logger.warning("completion_report_deferred", extra={"execution_id": execution_id, "error": str(exc)})
            async with self._factory() as db:
                deliveries = (await db.execute(select(WorkerCompletion.execution_id).where(
                    WorkerCompletion.status.in_(["completed", "failed", "interrupted"]),
                    WorkerCompletion.delivery_status == "pending")
                    .order_by(WorkerCompletion.updated_at, WorkerCompletion.execution_id)
                    .limit(max(1, min(limit, 100))))).scalars().all()
            for execution_id in deliveries:
                try:
                    result["delivered"] += int(await self._deliver(execution_id))
                except Exception as exc:
                    result["errors"] += 1
                    async with self._factory() as db:
                        await db.execute(update(WorkerCompletion).where(WorkerCompletion.execution_id == execution_id,
                            WorkerCompletion.delivery_status.in_(["pending", "sending"]))
                            .values(delivery_status="blocked" if isinstance(exc, WorkspaceError) else "pending", last_error=str(exc)))
                        await db.commit()
                    logger.warning("completion_push_deferred", extra={"execution_id": execution_id, "error": str(exc)})
        return result
